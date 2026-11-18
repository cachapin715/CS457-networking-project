#!/usr/bin/env python3
"""
client.py - Battleship game client (CS 457 Sprint 3)

Usage:
    python3 client.py -h
    python3 client.py -i server.chapin.edu -p 8080
    python3 client.py -i localhost -p 8080        (local testing)

The client resolves the server hostname via DNS, connects over TCP, and
then reacts to server messages (newline-delimited JSON, see the Sprint 1
protocol blueprint). It only prompts you when the server expects a move.
Type QUIT at any prompt (or press Ctrl+C) to leave the game.
"""

import argparse
import json
import random
import socket
import sys
import time

# ----------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------
CONNECT_TIMEOUT = 10  # seconds to wait for the TCP handshake
MAX_LINE_BYTES = 65536
WATER, SHIP, HIT, MISS = "~", "S", "X", "o"  # board symbols

# The 9 message types from the Sprint 1 protocol blueprint.
VALID_MSG_TYPES = {
    "CONNECT", "LOBBY_WAIT", "GAME_START", "SETUP", "ATTACK",
    "STATE_UPDATE", "GAME_OVER", "DISCONNECT", "ERROR",
}

# Abrupt-teardown errors we handle explicitly (no bare/broad except).
CONNECTION_ERRORS = (
    ConnectionResetError, BrokenPipeError, ConnectionAbortedError,
)


# ----------------------------------------------------------------------
# Protocol layer: framing + serialization (newline-delimited JSON)
# ----------------------------------------------------------------------
class ProtocolError(Exception):
    """A received line was not a valid protocol message."""


class QuitRequested(Exception):
    """Raised when the player types QUIT at a prompt."""


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


def make_disconnect(reason: str, text: str) -> dict:
    """Synthetic DISCONNECT handed to callers on EOF / connection errors."""
    return {
        "msg_type": "DISCONNECT",
        "player_id": "Server",
        "payload": {"reason": reason, "message": text},
        "timestamp": int(time.time()),
    }


def receive_message(rfile) -> dict:
    """Block until one full newline-terminated message arrives.

    readline() on the socket's buffered file reassembles partial TCP reads
    into complete lines. On TCP EOF (b"") or an abrupt reset this returns a
    DISCONNECT message instead of raising, so the caller has one code path.
    """
    try:
        line = rfile.readline(MAX_LINE_BYTES)
    except CONNECTION_ERRORS as err:
        return make_disconnect("CONNECTION_LOST",
                               f"Connection to the server was lost ({err}).")
    if not line or not line.endswith(b"\n"):  # b"" = TCP EOF
        return make_disconnect("TCP_EOF", "The server closed the connection.")
    return parse_message(line)


def send_message(sock, msg_type: str, player_id: str, payload: dict) -> bool:
    """Send one message. Returns False if the connection is already gone."""
    try:
        sock.sendall(serialize_message(msg_type, player_id, payload))
        return True
    except CONNECTION_ERRORS:
        return False


# ----------------------------------------------------------------------
# Board helpers and rendering
# ----------------------------------------------------------------------
def new_grid(size: int) -> list:
    """Create a size x size grid filled with water."""
    return [[WATER] * size for _ in range(size)]


def coord_name(row: int, col: int) -> str:
    """(0, 6) -> 'A7'."""
    return f"{chr(ord('A') + row)}{col + 1}"


def parse_coordinate(text: str, size: int):
    """'b7' -> (1, 6). Returns None if the text is not a cell on the board."""
    text = text.strip().upper()
    if len(text) < 2 or not text[0].isalpha() or not text[1:].isdecimal():
        return None
    row = ord(text[0]) - ord("A")
    col = int(text[1:]) - 1
    if 0 <= row < size and 0 <= col < size:
        return row, col
    return None


def grid_lines(grid: list) -> list:
    """Render one board as text lines (column numbers + lettered rows)."""
    size = len(grid)
    lines = ["    " + "".join(f"{col + 1:<3}" for col in range(size))]
    for row in range(size):
        label = chr(ord("A") + row)
        lines.append(f" {label}  " + "".join(f"{c:<3}" for c in grid[row]))
    return lines


def render_boards(tracking_grid: list, own_grid: list) -> str:
    """Draw the enemy (tracking) board and your own fleet side by side."""
    width = 4 + 3 * len(own_grid)
    left = ["ENEMY WATERS".center(width)] + grid_lines(tracking_grid)
    right = ["YOUR FLEET".center(width)] + grid_lines(own_grid)
    rows = zip(left, right)
    return "\n".join((lhs + "    " + rhs).rstrip() for lhs, rhs in rows)


def place_ship(grid: list, row: int, col: int, horizontal: bool, length: int):
    """Work out a ship's cells. Returns (cells, error_message)."""
    size = len(grid)
    cells = []
    for step in range(length):
        r = row if horizontal else row + step
        c = col + step if horizontal else col
        if r >= size or c >= size:
            return None, "That ship would hang off the board."
        if grid[r][c] != WATER:
            return None, "That ship overlaps another ship."
        cells.append((r, c))
    return cells, None


def ship_record(name: str, cells: list) -> dict:
    """Protocol form of a ship: name plus [row, col] start and end."""
    return {"name": name, "start": list(cells[0]), "end": list(cells[-1])}


def auto_place_fleet(fleet: list, size: int):
    """Randomly place every ship (the AUTO command). Returns (ships, grid)."""
    grid = new_grid(size)
    ships = []
    for ship in fleet:
        while True:  # a 10x10 board always has room, so this ends quickly
            row, col = random.randrange(size), random.randrange(size)
            horizontal = random.choice([True, False])
            cells, error = place_ship(grid, row, col, horizontal,
                                      ship["length"])
            if error is None:
                break
        for r, c in cells:
            grid[r][c] = SHIP
        ships.append(ship_record(ship["name"], cells))
    return ships, grid


# ----------------------------------------------------------------------
# Game client: reacts to server messages
# ----------------------------------------------------------------------
class BattleshipClient:
    """Holds the client-side view of the game and drives the UI."""

    def __init__(self, sock):
        self.sock = sock
        self.rfile = sock.makefile("rb")  # buffered reader for readline()
        self.player_id = "Unassigned"
        self.opponent_id = None
        self.board_size = 10
        self.fleet = []           # [{"name": ..., "length": ...}] from server
        self.own_grid = new_grid(10)       # my ships + enemy shots
        self.tracking_grid = new_grid(10)  # my shots at the enemy
        self.pending = None       # "SETUP" or "ATTACK": what we last sent
        self.turn_count = 0
        self.running = True

    # --- main loop -----------------------------------------------------
    def run(self) -> None:
        """Join the game, then handle server messages until it ends."""
        self.send("CONNECT", {"client": "battleship-cli"})
        print("Connected. Waiting for the server...")
        handlers = {
            "LOBBY_WAIT": self.on_lobby_wait,
            "GAME_START": self.on_game_start,
            "STATE_UPDATE": self.on_state_update,
            "GAME_OVER": self.on_game_over,
            "DISCONNECT": self.on_disconnect,
            "ERROR": self.on_error,
        }
        while self.running:
            try:
                message = receive_message(self.rfile)
            except ProtocolError as err:
                print(f"[WARN] Ignoring malformed server message: {err}")
                continue
            handler = handlers.get(message["msg_type"])
            if handler is not None:
                handler(message["payload"])

    def send(self, msg_type: str, payload: dict) -> None:
        """Send a message to the server, warning if the link is down."""
        if not send_message(self.sock, msg_type, self.player_id, payload):
            print("[WARN] Could not reach the server; connection may be lost.")

    def read_input(self, prompt: str) -> str:
        """input() that treats QUIT as a request to leave the game."""
        text = input(prompt).strip()
        if text.upper() == "QUIT":
            raise QuitRequested()
        return text

    def quit_game(self) -> None:
        """Graceful termination: tell the server we are leaving."""
        print("\nLeaving the game...")
        send_message(self.sock, "DISCONNECT", self.player_id,
                     {"reason": "PLAYER_QUIT"})

    def close(self) -> None:
        """Tear down the socket."""
        self.rfile.close()
        self.sock.close()

    # --- server message handlers --------------------------------------
    def on_lobby_wait(self, payload: dict) -> None:
        print(payload.get("message", "Waiting for Player 2..."))

    def on_game_start(self, payload: dict) -> None:
        """Server assigned our role; start ship placement."""
        self.player_id = payload.get("assigned_id", self.player_id)
        self.opponent_id = payload.get("opponent_id")
        self.board_size = payload.get("board_size", 10)
        self.fleet = payload.get("fleet", [])
        self.own_grid = new_grid(self.board_size)
        self.tracking_grid = new_grid(self.board_size)
        print(f"\n=== GAME START: you are {self.player_id} "
              f"(opponent: {self.opponent_id}) ===")
        self.do_setup()

    def on_state_update(self, payload: dict) -> None:
        """Apply the last attack, redraw, then prompt or wait for a turn."""
        self.pending = None
        last_attack = payload.get("last_attack")
        if last_attack:
            self.turn_count += 1
            self.apply_attack(payload.get("target_player"), last_attack)
        else:
            print("\nBoth fleets are placed - the battle begins!")
        self.show_status(payload)

        active = payload.get("active_turn")
        if active == self.player_id:
            print(">>> YOUR TURN <<<")
            self.prompt_attack()
        else:
            print(f"Waiting for {active} to strike...")

    def on_game_over(self, payload: dict) -> None:
        """Show the final shot, the result and the final score."""
        self.apply_attack(payload.get("target_player"),
                          payload.get("last_attack"))
        self.show_status(payload)
        if payload.get("winner") == self.player_id:
            print("*** VICTORY! You sank the entire enemy fleet! ***")
        else:
            print("*** DEFEAT - your whole fleet was sunk. ***")
        self.running = False

    def on_disconnect(self, payload: dict) -> None:
        print(f"\n[DISCONNECTED] {payload.get('message', 'Session ended.')}")
        self.running = False

    def on_error(self, payload: dict) -> None:
        """Show the server's ERROR; redo the move if it was rejected."""
        code = payload.get("error_code", "UNKNOWN")
        print(f"[SERVER ERROR] {code}: {payload.get('message', '')}")
        if code == "SERVER_FULL":
            self.running = False
        elif code == "INVALID_PLACEMENT" and self.pending == "SETUP":
            self.do_setup()
        elif code == "INVALID_COORDINATE" and self.pending == "ATTACK":
            self.prompt_attack()

    # --- setup phase ---------------------------------------------------
    def do_setup(self) -> None:
        """Ask the player to place the fleet, then send SETUP."""
        self.pending = "SETUP"
        print("Place your ships. Enter <cell> <H|V>, e.g. 'B3 H' "
              "(H = rightward, V = downward).")
        print("Type AUTO to place the whole fleet randomly.\n")
        ships, grid = self.prompt_fleet()
        self.own_grid = grid
        self.send("SETUP", {"ships": ships})
        print("Fleet submitted. Waiting for the opponent to finish...")

    def prompt_fleet(self):
        """Prompt for each ship with local checks. Returns (ships, grid)."""
        grid = new_grid(self.board_size)
        ships = []
        for ship in self.fleet:
            name, length = ship["name"], ship["length"]
            while True:
                entry = self.read_input(f"Place {name} ({length} cells) > ")
                if entry.upper() == "AUTO":
                    return auto_place_fleet(self.fleet, self.board_size)
                cells, error = self.parse_placement(entry, grid, length)
                if error is None:
                    break
                print(f"  {error}")
            for r, c in cells:
                grid[r][c] = SHIP
            ships.append(ship_record(name, cells))
            print("\n".join(grid_lines(grid)))
        return ships, grid

    def parse_placement(self, entry: str, grid: list, length: int):
        """Turn 'B3 H' into ship cells. Returns (cells, error_message)."""
        parts = entry.split()
        if len(parts) != 2 or parts[1].upper() not in ("H", "V"):
            return None, "Use the form <cell> <H|V>, e.g. B3 H"
        start = parse_coordinate(parts[0], self.board_size)
        if start is None:
            return None, f"'{parts[0]}' is not a cell on the board."
        return place_ship(grid, start[0], start[1],
                          parts[1].upper() == "H", length)

    # --- attack phase --------------------------------------------------
    def prompt_attack(self) -> None:
        """Ask for a target cell (checked locally), then send ATTACK."""
        self.pending = "ATTACK"
        while True:
            entry = self.read_input("Enter target cell (e.g. B7) > ")
            target = parse_coordinate(entry, self.board_size)
            if target is None:
                print("  Not a valid cell. Use a letter and a number, e.g. B7")
            elif self.tracking_grid[target[0]][target[1]] != WATER:
                print("  You already fired there. Pick another cell.")
            else:
                break
        self.send("ATTACK", {"row": target[0], "col": target[1]})

    def apply_attack(self, target_player, last_attack) -> None:
        """Mark a shot on the right board and announce the result."""
        if not last_attack:
            return
        row, col = last_attack["row"], last_attack["col"]
        result, sunk = last_attack["result"], last_attack.get("sunk_ship")
        symbol = HIT if result == "HIT" else MISS
        cell = coord_name(row, col)

        if target_player == self.player_id:  # opponent fired at me
            self.own_grid[row][col] = symbol
            text = f"Opponent fired at {cell}: {result}"
            text += f" - your {sunk} was sunk!" if sunk else ""
        else:                                # I fired at the opponent
            self.tracking_grid[row][col] = symbol
            text = f"You fired at {cell}: {result}"
            text += f" - you sank their {sunk}!" if sunk else ""
        print(f"\n{text}")

    def show_status(self, payload: dict) -> None:
        """Print both boards plus the score summary."""
        scores = payload.get("scores", {})
        afloat = payload.get("ships_remaining", {})
        print()
        print(render_boards(self.tracking_grid, self.own_grid))
        print(f"\nTurn {self.turn_count + 1} | "
              f"Score  You: {scores.get(self.player_id, 0)}  "
              f"Opponent: {scores.get(self.opponent_id, 0)} | "
              f"Ships afloat  You: {afloat.get(self.player_id, '?')}  "
              f"Opponent: {afloat.get(self.opponent_id, '?')}")


# ----------------------------------------------------------------------
# Command-line handling, DNS resolution, connection setup
# ----------------------------------------------------------------------
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
    """Parse -h / -i / -p command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Battleship game client (TCP, newline-delimited JSON).",
        epilog="Example: python3 client.py -i server.chapin.edu -p 8080",
    )
    parser.add_argument(
        "-i", "--hostname", required=True, metavar="HOSTNAME",
        help="server DNS hostname or IP address (e.g. server.chapin.edu)")
    parser.add_argument(
        "-p", "--port", type=port_number, required=True, metavar="PORT",
        help="server TCP port (1024-65535)")
    return parser.parse_args()


def resolve_hostname(hostname: str) -> str:
    """Resolve the server name via DNS; exit(1) cleanly if that fails."""
    try:
        return socket.gethostbyname(hostname)
    except (socket.gaierror, UnicodeError) as err:
        # UnicodeError covers malformed names (e.g. an over-long label).
        print(f"[ERROR] Unable to resolve DNS hostname '{hostname}'.")
        print("Please check your Cisco router DNS configuration "
              "('ip dns server' / 'ip host') or verify domain spelling.")
        print(f"        (resolver said: {err})")
        sys.exit(1)


def connect_to_server(server_ip: str, port: int):
    """Open the TCP connection; exit(1) with a clear message on failure."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(CONNECT_TIMEOUT)
    try:
        sock.connect((server_ip, port))
    except ConnectionRefusedError:
        print(f"[ERROR] Connection refused by {server_ip}:{port}. "
              "Is the server running on that port?")
    except socket.timeout:
        print(f"[ERROR] Timed out connecting to {server_ip}:{port}. "
              "Check routing/firewall between the subnets.")
    except OSError as err:
        print(f"[ERROR] Could not connect to {server_ip}:{port}: {err}")
    else:
        sock.settimeout(None)  # back to blocking mode for the game
        return sock
    sock.close()
    sys.exit(1)


def main() -> None:
    args = parse_args()
    server_ip = resolve_hostname(args.hostname)
    print(f"[INFO] Resolved '{args.hostname}' -> {server_ip}")
    sock = connect_to_server(server_ip, args.port)
    print(f"[INFO] Connected to {args.hostname}:{args.port}")

    client = BattleshipClient(sock)
    try:
        client.run()
    except (QuitRequested, KeyboardInterrupt, EOFError):
        client.quit_game()  # QUIT, Ctrl+C, or stdin closed
    finally:
        client.close()


if __name__ == "__main__":
    main()
