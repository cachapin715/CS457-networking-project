## Application-Layer Messaging Protocol Blueprint

### Message Transport & Serialization Format
- **Transport Protocol:** TCP
- **Serialization Format:** JSON
- **Framing Mechanism:** Newline-delimited (`\n`) JSON payloads
---

### Message Schema Definitions

#### Message Types:
1. `CONNECT` (Client -> Server): Request to join the game room.
2. `LOBBY_WAIT` (Server -> Client): Notification that server is waiting for Player 2.
3. `GAME_START` (Server -> Clients): Game initiated, assigns roles (Player 1 vs Player 2).
4. `SETUP` (Clients -> Server) Players select coordinates to place their 5 ships on the board.
5. `ATTACK` (Clients -> Server): Active player selects a coordinate to strike on enemy player's board.
6. `STATE_UPDATE` (Server -> Clients): Broadcast current game board / state and active player turn.
7. `GAME_OVER` (Server -> Clients): Victory / Loss notification based on which player is the winner / loser
8. `DISCONNECT` (Server -> Clients): Server notice that one of the players has quit / dropped connection.
9. `ERROR` (Server -> Client): Invalid move or malformed packet error.
---

#### Example JSON Protocol Schema:
```json
{
  "msg_type": "SETUP",
  "player_id": "Player_1",
  "payload": {
    "ships": [
      {"name": "Carrier", "start": [0, 0], "end": [0, 4]},
      {"name": "Battleship", "start": [1, 0], "end": [1, 3]}
    ]
  },
  "timestamp": 1727000000
}

{
  "msg_type": "ATTACK",
  "player_id": "Player_1",
  "payload": {
    "row": 2,
    "col": 2
  },
  "timestamp": 1727000000
}

{
  "msg_type": "STATE_UPDATE",
  "player_id": "Server",
  "payload": {
    "target_player": "Player_2",
    "last_attack": {"row": 2, "col": 2, "result": "HIT", "sunk_ship": null},
    "active_turn": "Player_2"
  },
  "timestamp": 1727000005
}

{
  "msg_type": "ERROR",
  "player_id": "Server",
  "payload": {
    "error_code": "NOT_YOUR_TURN",
    "message": "It is currently Player 2's turn to strike."
  },
  "timestamp": 1727000010
}
```
---

### Raw Wire Stream Examples

Each frame below represents a single line transmitted over the TCP socket stream, terminated with `\n`:

**1. SETUP Packet:**
```json
{"msg_type":"SETUP","player_id":"Player_1","payload":{"ships":[{"name":"Carrier","start":[0,0],"end":[0,4]},{"name":"Battleship","start":[1,0],"end":[1,3]}]},"timestamp":1727000000}\n
```

**2. ATTACK Packet:**
```json
{"msg_type":"ATTACK","player_id":"Player_1","payload":{"row":2,"col":2},"timestamp":1727000000}\n
```

**3. STATE_UPDATE Packet:**
```json
{"msg_type":"STATE_UPDATE","player_id":"Server","payload":{"target_player":"Player_2","last_attack":{"row":2,"col":2,"result":"HIT","sunk_ship":null},"active_turn":"Player_2"},"timestamp":1727000005}\n
```

**4. ERROR Packet:**
```json
{"msg_type":"ERROR","player_id":"Server","payload":{"error_code":"NOT_YOUR_TURN","message":"It is currently Player 2's turn to strike."},"timestamp":1727000010}\n
```
---

### Connection Termination Logic
The network code will be checking for either a graceful or abrupt teardown in real time during a game session and handle each appropriately. In the messaging blueprint and the FSM diagram I have handled both of these with the same state and flow of control looking for either a player disconnect message for a gracefull termination, or a socket exception or TCP EOF for an abrupt termination. This logic will be handled in the TERMINATED state shown in the project FSM document.

For a graceful termination, a player will be prompted to enter a "QUIT" prompt into the terminal which will send a DISCONNECT message across the network, terminating the session for both players, and doing the necessary network teardown and cleanup protocols (TCP FIN handshake, etc.)

For abrupt termination the network will be looking for TCP EOF value b"" or a socket exception, and handle each appropriately inside the TERMINATED state of the FSM. For a TCP EOF value "if not data: break" will be added as a check to break out of an infinite loop. For a socket exception care will be taken to catch the possible exceptions and handle each gracefully with an appropriate error message, then terminating the network connection. Examples of these handled in the socket code are provided below.

```python
# Standard pattern for detecting TCP connection termination:
data = sock.recv(1024)
if not data:
    # Remote peer closed connection cleanly (TCP FIN received)
    logger.info("Remote peer disconnected (EOF received).")
    sock.close()
    handle_client_disconnect(player_id)
```

```python
try:
    data = recv_exact(sock, msg_len)
    if data is None:
        trigger_state_transition("CLIENT_DISCONNECTED")
except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError) as e:
    logger.warning(f"Connection lost abruptly: {e}")
    trigger_state_transition("CLIENT_DISCONNECTED")
```
