#!/usr/bin/env python3
"""
server.py - Battleship game server (CS 457 Sprint 3)

Usage:
    python3 server.py -h
    python3 server.py -p 8080

Design summary (Sprint 1 protocol/FSM + Sprint 2 concurrency docs):
  * TCP stream sockets; every message is one newline-delimited JSON line.
  * Multi-threaded: the main thread accepts connections and each of the two
    players gets a dedicated client thread (blocking readline per socket).
  * A GameSession holds all shared game state. Every read/write of that
    state happens inside `with state_lock:` (critical section).
  * FSM: LOBBY -> SETUP_PHASE -> ATTACK_PHASE <-> STATE_UPDATE -> GAME_OVER,
    and TERMINATED on any disconnect / TCP EOF.
"""

import argparse
import json
import logging
import socket
import sys
import threading
import time

logger = logging.getLogger("battleship")

# ----------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------
BOARD_SIZE = 10
FLEET = [  # (ship name, length); same order as the player_scores HP list
    ("Carrier", 5),
    ("Battleship", 4),
    ("Destroyer", 3),
    ("Submarine", 3),
    ("Patrol Boat", 2),
]
SHIP_LENGTHS = dict(FLEET)
SHIP_NAMES = [name for name, _ in FLEET]
SCORE_INDEX = len(FLEET)  # last slot in a player_scores list = the score

PLAYER_IDS = ("Player_1", "Player_2")
SERVER_ID = "Server"

# The 9 message types from the Sprint 1 protocol blueprint.
VALID_MSG_TYPES = {
    "CONNECT", "LOBBY_WAIT", "GAME_START", "SETUP", "ATTACK",
    "STATE_UPDATE", "GAME_OVER", "DISCONNECT", "ERROR",
}
MAX_LINE_BYTES = 65536  # guard against a "line" that never ends

# FSM states (fsm_specification.md).
LOBBY = "LOBBY"
SETUP_PHASE = "SETUP_PHASE"
ATTACK_PHASE = "ATTACK_PHASE"
STATE_UPDATE = "STATE_UPDATE"
GAME_OVER = "GAME_OVER"
TERMINATED = "TERMINATED"
FINISHED_STATES = (GAME_OVER, TERMINATED)

# Abrupt-teardown errors we handle explicitly (no bare/broad except).
CONNECTION_ERRORS = (
    ConnectionResetError, BrokenPipeError, ConnectionAbortedError,
)


# ----------------------------------------------------------------------
# Protocol layer: framing + serialization (newline-delimited JSON)
# ----------------------------------------------------------------------
class ProtocolError(Exception):
    """A received line was not a valid protocol message."""


def serialize_message(msg_type: str, player_id: str, payload: dict) -> bytes:
    """Build one wire frame: compact JSON terminated by a newline."""
    if msg_type not in VALID_MSG_TYPES:
        raise ValueError(f"Unknown msg_type: {msg_type}")
    message = {
        "msg_type": msg_type,
        "player_id": player_id,
        "payload": payload,
        "timestamp": int(time.time()),
    }
    return (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")


def parse_message(line: bytes) -> dict:
    """Decode one received line; raise ProtocolError if it is invalid."""
    try:
        message = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as err:
        raise ProtocolError(f"not valid JSON ({err})") from err
    if not isinstance(message, dict):
        raise ProtocolError("message must be a JSON object")
    if message.get("msg_type") not in VALID_MSG_TYPES:
        raise ProtocolError("missing or unknown msg_type")
    if not isinstance(message.get("payload"), dict):
        raise ProtocolError("payload must be a JSON object")
    return message


def make_disconnect(player_id: str, reason: str, text: str) -> dict:
    """Synthetic DISCONNECT handed to callers on EOF / connection errors."""
    return {
        "msg_type": "DISCONNECT",
        "player_id": player_id,
        "payload": {"reason": reason, "message": text},
        "timestamp": int(time.time()),
    }


def receive_message(rfile, player_id: str) -> dict:
    """Block until one full newline-terminated message arrives.

    readline() on the socket's buffered file reassembles partial TCP reads
    into complete lines. On TCP EOF (b"") or an abrupt reset this returns a
    DISCONNECT message instead of raising, so the caller has one code path.
    """
    try:
        line = rfile.readline(MAX_LINE_BYTES)
    except CONNECTION_ERRORS as err:
        logger.warning("%s: connection lost abruptly (%s)", player_id, err)
        return make_disconnect(player_id, "CONNECTION_LOST", str(err))

    if not line:  # b"" means the peer closed the connection (TCP EOF)
        logger.info("%s: TCP EOF received", player_id)
        return make_disconnect(player_id, "TCP_EOF", "Connection closed.")
    if not line.endswith(b"\n"):
        if len(line) >= MAX_LINE_BYTES:
            logger.warning("%s: message too long, dropping", player_id)
            return make_disconnect(player_id, "MESSAGE_TOO_LONG", "Bad frame.")
        # Stream ended in the middle of a message: treat as EOF.
        logger.info("%s: TCP EOF mid-message", player_id)
        return make_disconnect(player_id, "TCP_EOF", "Connection closed.")
    return parse_message(line)


def send_message(sock, msg_type: str, payload: dict) -> bool:
    """Send one server message. Returns False if the peer is already gone."""
    try:
        sock.sendall(serialize_message(msg_type, SERVER_ID, payload))
        return True
    except CONNECTION_ERRORS as err:
        logger.warning("Send of %s failed: %s", msg_type, err)
        return False


def send_error(sock, error_code: str, text: str) -> None:
    """Send an ERROR message (invalid move, malformed packet, etc.)."""
    send_message(sock, "ERROR", {"error_code": error_code, "message": text})


def half_close(sock) -> None:
    """Send FIN after any queued data so the client sees a clean EOF."""
    try:
        sock.shutdown(socket.SHUT_WR)
    except OSError:
        pass  # socket already closed / not connected: nothing to do


# ----------------------------------------------------------------------
# Battleship rules (pure helpers, no networking)
# ----------------------------------------------------------------------
def other_player(player_id: str) -> str:
    """Return the opponent's id."""
    return PLAYER_IDS[1] if player_id == PLAYER_IDS[0] else PLAYER_IDS[0]


def is_int(value) -> bool:
    """True for real ints (JSON booleans are not accepted)."""
    return isinstance(value, int) and not isinstance(value, bool)


def is_valid_cell(row, col) -> bool:
    """True if (row, col) are ints that lie on the board."""
    return (is_int(row) and is_int(col)
            and 0 <= row < BOARD_SIZE and 0 <= col < BOARD_SIZE)


def validate_placement(ships):
    """Check a SETUP fleet. Returns (cells_by_ship, error_message).

    error_message is None when the fleet is legal: all 5 ships, correct
    lengths, straight lines, on the board, and no overlaps.
    """
    if not isinstance(ships, list) or len(ships) != len(FLEET):
        return None, f"You must place exactly {len(FLEET)} ships."

    cells_by_ship = {}
    occupied = set()
    for ship in ships:
        if not isinstance(ship, dict):
            return None, "Each ship must have a name, start and end."
        name = ship.get("name")
        if not isinstance(name, str) or name not in SHIP_LENGTHS:
            return None, f"Unknown ship name: {name}"
        if name in cells_by_ship:
            return None, f"{name} was placed more than once."

        start, end = ship.get("start"), ship.get("end")
        if not (isinstance(start, list) and len(start) == 2
                and isinstance(end, list) and len(end) == 2
                and is_valid_cell(*start) and is_valid_cell(*end)):
            return None, f"{name}: start/end must be [row, col] on the board."
        (row1, col1), (row2, col2) = start, end
        if row1 != row2 and col1 != col2:
            return None, f"{name}: ships must be horizontal or vertical."

        cells = set()
        for row in range(min(row1, row2), max(row1, row2) + 1):
            for col in range(min(col1, col2), max(col1, col2) + 1):
                cells.add((row, col))
        expected_length = SHIP_LENGTHS[name]
        if len(cells) != expected_length:
            return None, f"{name} must cover exactly {expected_length} cells."
        if cells & occupied:
            return None, f"{name} overlaps another ship."

        occupied |= cells
        cells_by_ship[name] = cells
    return cells_by_ship, None


def apply_attack(board: dict, row: int, col: int):
    """Record a shot on `board`. Returns (result, sunk_ship_name_or_None)."""
    board["shots"].add((row, col))
    for name, cells in board["ships"].items():
        if (row, col) in cells:
            sunk_ship = name if cells <= board["shots"] else None
            return "HIT", sunk_ship
    return "MISS", None


def check_all_sunk(board: dict) -> bool:
    """True when every cell of every ship has been hit."""
    return all(cells <= board["shots"] for cells in board["ships"].values())


# ----------------------------------------------------------------------
# GameSession: shared state + FSM transitions
# ----------------------------------------------------------------------
class GameSession:
    """Shared state for one two-player game and the FSM that drives it.

    Methods ending in `_locked` assume the caller already holds
    `state_lock`. threading.Lock is not re-entrant, so they never acquire
    it themselves.
    """

    def __init__(self):
        self.state_lock = threading.Lock()
        self.game_state = LOBBY
        self.connected_clients = {}  # player_id -> client socket
        self.ready_players = set()   # players whose CONNECT has arrived
        self.boards = {}             # player_id -> {"ships": {}, "shots": set}
        self.active_turn = None      # player_id that may ATTACK right now
        # [Carrier_HP, Battleship_HP, Destroyer_HP, Sub_HP, Patrol_HP, Score]
        self.player_scores = {
            player_id: [length for _, length in FLEET] + [0]
            for player_id in PLAYER_IDS
        }

    # --- called by the main (accept) thread ---------------------------
    def assign_player(self, conn):
        """Give a new connection the next free player slot (or None)."""
        with self.state_lock:  # CRITICAL SECTION: connected_clients
            if self.game_state != LOBBY:
                return None
            for player_id in PLAYER_IDS:
                if player_id not in self.connected_clients:
                    self.connected_clients[player_id] = conn
                    return player_id
            return None

    def is_finished(self) -> bool:
        """True once the session reached GAME_OVER or TERMINATED."""
        with self.state_lock:
            return self.game_state in FINISHED_STATES

    # --- called by the per-client threads -----------------------------
    def process_message(self, player_id: str, message: dict) -> None:
        """Dispatch one validated client message to the right FSM handler."""
        msg_type, payload = message["msg_type"], message["payload"]
        with self.state_lock:  # CRITICAL SECTION: all game state
            conn = self.connected_clients.get(player_id)
            if conn is None or self.game_state in FINISHED_STATES:
                return  # session already over: ignore stray packets
            if msg_type == "CONNECT":
                self.on_connect_locked(player_id, conn)
            elif msg_type == "SETUP":
                self.on_setup_locked(player_id, conn, payload)
            elif msg_type == "ATTACK":
                self.on_attack_locked(player_id, conn, payload)
            else:
                send_error(conn, "MALFORMED_PACKET",
                           f"{msg_type} is not a valid client message.")

    def reject_malformed(self, player_id: str, reason: str) -> None:
        """Reply to an unparseable line with an ERROR (state unchanged)."""
        with self.state_lock:
            conn = self.connected_clients.get(player_id)
            if conn is not None:
                send_error(conn, "MALFORMED_PACKET", reason)

    def handle_disconnect(self, player_id: str, reason: str) -> None:
        """Remove a player; end the session if it was still in progress."""
        with self.state_lock:  # CRITICAL SECTION: clients + game_state
            if player_id not in self.connected_clients:
                return  # already handled
            del self.connected_clients[player_id]
            if self.game_state in FINISHED_STATES:
                logger.info("%s connection closed (session over).", player_id)
                return
            logger.info("%s left mid-session (%s).", player_id, reason)
            self.set_state_locked(TERMINATED, f"{player_id} disconnected")
            for sock in self.connected_clients.values():
                send_message(sock, "DISCONNECT", {
                    "reason": reason,
                    "message": f"{player_id} left the game. Session over.",
                })
                half_close(sock)

    # --- FSM handlers (state_lock must be held) -----------------------
    def set_state_locked(self, new_state: str, why: str) -> None:
        """Perform and log one FSM transition."""
        logger.info("FSM: %s -> %s (%s)", self.game_state, new_state, why)
        self.game_state = new_state

    def broadcast_locked(self, msg_type: str, payload: dict) -> None:
        """Send the same message to every connected player."""
        for sock in self.connected_clients.values():
            send_message(sock, msg_type, payload)

    def on_connect_locked(self, player_id: str, conn) -> None:
        """LOBBY: wait for Player 2, then move to SETUP_PHASE."""
        if self.game_state != LOBBY or player_id in self.ready_players:
            send_error(conn, "WRONG_PHASE", "Already connected to a game.")
            return
        self.ready_players.add(player_id)
        logger.info("%s joined the lobby.", player_id)

        if len(self.ready_players) < len(PLAYER_IDS):
            send_message(conn, "LOBBY_WAIT",
                         {"message": "Waiting for Player 2 to connect..."})
            return

        self.set_state_locked(SETUP_PHASE, "both players connected")
        for pid, sock in self.connected_clients.items():
            send_message(sock, "GAME_START", {
                "assigned_id": pid,
                "opponent_id": other_player(pid),
                "board_size": BOARD_SIZE,
                "fleet": [{"name": n, "length": ln} for n, ln in FLEET],
            })

    def on_setup_locked(self, player_id: str, conn, payload: dict) -> None:
        """SETUP_PHASE: validate a fleet; start the battle when both are in."""
        if self.game_state != SETUP_PHASE or player_id in self.boards:
            send_error(conn, "WRONG_PHASE",
                       "Ships can only be placed once, during setup.")
            return
        cells_by_ship, error = validate_placement(payload.get("ships"))
        if error is not None:
            logger.info("%s sent an invalid placement: %s", player_id, error)
            send_error(conn, "INVALID_PLACEMENT", error)  # SETUP self-loop
            return

        self.boards[player_id] = {"ships": cells_by_ship, "shots": set()}
        logger.info("%s placed a valid fleet.", player_id)

        if len(self.boards) == len(PLAYER_IDS):
            self.active_turn = PLAYER_IDS[0]  # Player 1 fires first
            self.set_state_locked(ATTACK_PHASE, "both fleets placed")
            self.broadcast_locked(
                "STATE_UPDATE", self.state_payload_locked(None, None))

    def on_attack_locked(self, player_id: str, conn, payload: dict) -> None:
        """ATTACK_PHASE: validate the shot, update boards, advance the FSM."""
        if self.game_state != ATTACK_PHASE:
            send_error(conn, "WRONG_PHASE",
                       "Attacks are only allowed during the attack phase.")
            return
        if self.active_turn != player_id:
            send_error(conn, "NOT_YOUR_TURN",
                       f"It is currently {self.active_turn}'s turn to strike.")
            return

        row, col = payload.get("row"), payload.get("col")
        if not is_valid_cell(row, col):
            send_error(conn, "INVALID_COORDINATE",
                       f"Row and col must be integers 0-{BOARD_SIZE - 1}.")
            return
        target_id = other_player(player_id)
        target_board = self.boards[target_id]
        if (row, col) in target_board["shots"]:
            send_error(conn, "INVALID_COORDINATE",
                       "You already fired at that coordinate.")
            return

        # Valid attack: ATTACK_PHASE -> STATE_UPDATE (boards get updated).
        self.set_state_locked(STATE_UPDATE, f"{player_id} fired")
        result, sunk_ship = apply_attack(target_board, row, col)
        self.refresh_hp_locked(target_id)
        last_attack = {"row": row, "col": col,
                       "result": result, "sunk_ship": sunk_ship}
        logger.info("%s attacked (%d,%d): %s%s", player_id, row, col, result,
                    f" - sunk {sunk_ship}" if sunk_ship else "")

        if check_all_sunk(target_board):
            # Final enemy ship sunk: STATE_UPDATE -> GAME_OVER.
            self.player_scores[player_id][SCORE_INDEX] += 1
            self.set_state_locked(GAME_OVER, f"{player_id} sank the fleet")
            over = self.state_payload_locked(target_id, last_attack)
            over.update({"winner": player_id, "loser": target_id})
            self.broadcast_locked("GAME_OVER", over)
            for sock in self.connected_clients.values():
                half_close(sock)  # clients see EOF after GAME_OVER
        else:
            # Ships remain: STATE_UPDATE -> ATTACK_PHASE, turn passes.
            self.active_turn = target_id
            self.set_state_locked(ATTACK_PHASE, "ships remain, turn passes")
            self.broadcast_locked(
                "STATE_UPDATE", self.state_payload_locked(target_id,
                                                          last_attack))

    # --- score / payload helpers (state_lock must be held) ------------
    def refresh_hp_locked(self, player_id: str) -> None:
        """Recompute each ship's remaining HP in player_scores."""
        board = self.boards[player_id]
        for index, name in enumerate(SHIP_NAMES):
            remaining = board["ships"][name] - board["shots"]
            self.player_scores[player_id][index] = len(remaining)

    def state_payload_locked(self, target_player, last_attack) -> dict:
        """STATE_UPDATE / GAME_OVER body shared by both players."""
        scores = {}
        ships_remaining = {}
        for player_id in PLAYER_IDS:
            hp_list = self.player_scores[player_id]
            scores[player_id] = hp_list[SCORE_INDEX]
            ships_remaining[player_id] = sum(
                1 for hp in hp_list[:SCORE_INDEX] if hp > 0)
        return {
            "target_player": target_player,
            "last_attack": last_attack,
            "active_turn": self.active_turn,
            "game_state": self.game_state,
            "scores": scores,
            "ships_remaining": ships_remaining,
        }


# ----------------------------------------------------------------------
# Per-client thread + main accept loop
# ----------------------------------------------------------------------
def handle_client(session: GameSession, player_id: str, conn) -> None:
    """Thread body: read this player's messages until they disconnect."""
    rfile = conn.makefile("rb")  # buffered reader -> readline() framing
    reason = "UNKNOWN"
    try:
        while True:
            try:
                message = receive_message(rfile, player_id)
            except ProtocolError as err:
                session.reject_malformed(player_id, str(err))
                continue
            if message["msg_type"] == "DISCONNECT":
                reason = message["payload"].get("reason", "PLAYER_QUIT")
                break  # graceful QUIT, TCP EOF, or connection reset
            session.process_message(player_id, message)
    finally:
        session.handle_disconnect(player_id, reason)  # -> TERMINATED if live
        rfile.close()
        conn.close()


def reject_connection(conn) -> None:
    """Turn away a client when a game is already full or in progress."""
    send_error(conn, "SERVER_FULL",
               "A game is already in progress. Try again later.")
    conn.close()


def port_number(value: str) -> int:
    """argparse type: a TCP port in the range 1024-65535."""
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"'{value}' is not a valid port number") from None
    if not 1024 <= port <= 65535:
        raise argparse.ArgumentTypeError(
            f"port must be between 1024 and 65535 (got {port})")
    return port


def parse_args():
    """Parse -h / -p command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Battleship game server (TCP, newline-delimited JSON).",
        epilog="Example: python3 server.py -p 8080",
    )
    parser.add_argument(
        "-p", "--port", type=port_number, required=True, metavar="PORT",
        help="TCP port to listen on (1024-65535); binds to 0.0.0.0")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, datefmt="%H:%M:%S",
                        format="%(asctime)s [%(threadName)s] %(message)s")

    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if sys.platform == "win32":
        # On Windows, SO_REUSEADDR lets us bind a port another program is
        # already using (and clients may reach that program instead).
        # Exclusive mode makes bind() fail loudly in that case.
        server_sock.setsockopt(
            socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    else:
        # Linux/macOS: allow quick restarts while old sockets are in TIME_WAIT.
        server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server_sock.bind(("0.0.0.0", args.port))
    except OSError as err:
        print(f"[ERROR] Cannot bind to port {args.port}: {err}")
        sys.exit(1)
    server_sock.listen()
    logger.info("Battleship server listening on 0.0.0.0:%d", args.port)

    session = GameSession()
    try:
        while True:  # accept loop (main thread)
            conn, addr = server_sock.accept()
            player_id = session.assign_player(conn)
            if player_id is None and session.is_finished():
                session = GameSession()  # previous game ended: start fresh
                player_id = session.assign_player(conn)
            if player_id is None:
                logger.info("Rejected %s: game full / in progress", addr)
                reject_connection(conn)
                continue
            logger.info("Accepted %s from %s:%d", player_id, *addr)
            threading.Thread(target=handle_client, name=player_id,
                             args=(session, player_id, conn),
                             daemon=True).start()
    except KeyboardInterrupt:
        logger.info("Shutting down (Ctrl+C).")
    finally:
        server_sock.close()


if __name__ == "__main__":
    main()