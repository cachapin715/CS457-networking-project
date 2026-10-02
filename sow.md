# CS 457 Project Statement of Work (SOW) & Protocol Specification Template

**Student Name:** Christopher Chapin 
**Date:** 2026-09-18  
**Course:** CS 457 - Computer Networks  
**Target Server Domain:** `server.chapin.edu`  

---

## 1. Game Selection & Scope (Sprint 0)

> Planning is going to be an iterative process through the sprints so you don't have to have all the details now. Focus on big overview concepts. You will be updating the SOW as we plan.
> You have a lot of freedom to choose a game. There are a couple caveats.  

> - It must run in the console. The lab nodes won't be able to handle extensive graphics.
> - It has to be self-contained. You can use a internet-connector to download you code, but because the architecture must run 5 nodes you won't be able to run 
> - You are encouraged to use python, but I'm not going to make it a strict requirement. The instructor and TA's ability to help with C or Rust, etc will be diminished in other languages.

### 1.1 Game Overview
- **Chosen Game:** Battleship
- **Player Capacity:** 2 Players (Simulated via 2 CML Client nodes)
- **Game Summary:** Each player gets a board with 5 ships. A Carrier (5 hits), Battleship (4 hits), Destroyer (3 hits), Submarine (3 hits),
                    and a Patrol Boat (2 hits). Ships can be placed on the board grid wherever you want horizontally or vertically, but not
                    diagonally. Ships cannot overlap or hang off the board and once the game starts ships cannot be moved. The overall goal is
                    to correctly guess the locations of the enemy's ships and hit them with a strike at that location before they do the same to you. 
                    When all of the enemy ship's hit points are depleted and the ships are sunk, the game is over and you win.
### 1.2 Core Game Rules & Win/Draw Conditions
- **Turn Mechanics:** Players take turns guessing locations on the other player's board. If a player correctly guesses the location of an enemy
                      ship that's a hit, otherwise a miss. Hits and misses are tracked in real time over the network on the player boards, so over
                      time players get an idea of where they have already searched on the board and where the ships are likely hidden.
- **Victory Condition:** The first player to sink all of the enemy player's ships is the winner. When all of the hit points are depleted on a given
                         ship, that ship is sunk.
- **Draw/Tie Condition:** There is no draw/tie condition in standard Battleship rules. One player will always be the first sink all of the ships of
                          the other player, deciding a victor. So no draw conditions will need to be factored into the game and network design.

---

## 2. Application-Layer Messaging Protocol Blueprint (Sprint 1 Deliverable)

### 2.1 Message Transport & Serialization Format
- **Transport Protocol:** TCP
- **Serialization Format:** JSON
- **Framing Mechanism:** Newline-delimited (`\n`) JSON payloads

### 2.2 Message Schema Definitions

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

#### Example JSON Protocol Schema:
```json
{
  "msg_type": "ATTACK",
  "player_id": "Player_1",
  "payload": {
    "row": 2,
    "col": 2
  },
  "timestamp": 1727000000
}
```

---

### 2.3 Game State Machine (FSM) Design (Sprint 1 Deliverable)
- **State Transitions:** 

```mermaid
stateDiagram-v2

    [*] --> LOBBY : Server started and listening for players

    LOBBY --> LOBBY : Player 1 connects (Wait for Player 2)
    LOBBY --> SETUP_PHASE : Player 2 connects (Proceed to game setup)
    LOBBY --> TERMINATED : Player disconnected / TCP EOF (End game session)

    SETUP_PHASE --> SETUP_PHASE : A player sends invalid placement (Redo setup)
    SETUP_PHASE --> ATTACK_PHASE : Both players send valid placement (Game now in progress)
    SETUP_PHASE --> TERMINATED : Player disconnected / TCP EOF (End game session)

    ATTACK_PHASE --> ATTACK_PHASE : Active player attacks with invalid coordinate (Redo attack)
    ATTACK_PHASE --> STATE_UPDATE : Valid<br/>attack<br/>(Update<br/>board<br/>state)
    ATTACK_PHASE --> TERMINATED : Player disconnected / TCP EOF (End game session)

    STATE_UPDATE --> ATTACK_PHASE : Surviving<br/>ships<br/>after<br/>player<br/>attack<br/>(Continue<br/>game)
    STATE_UPDATE --> GAME_OVER : Final Enemy Ship Sunk (Game Over)

    GAME_OVER --> [*]
    TERMINATED --> [*]
  ```

---

## 3. Game Behavior & Server Concurrency Architecture (Sprint 2 Deliverable)

### 3.1 Server Concurrency Strategy
- **Architecture Choice:** [Multi-Threading (`threading.Thread`) OR Non-blocking I/O multiplexing (`select.select` / `selectors`)]
- **Synchronization Logic:** Explain how shared game state and client list are thread-safe (e.g. `threading.Lock`) to prevent race conditions during turn processing.

### 3.2 State & Score Synchronization Across Clients
- **Turn Enforcement:** Detail how the server validates active player ID before processing moves and broadcasts updated turn notifications to all clients.
- **Score & Board Synchronization:** Describe how state broadcasts keep client screens synchronized in real time.

---

## 4. Coding & AI Implementation Plan (Sprint 3)

- **Permitted AI Tools:** [e.g., GitHub Copilot, ChatGPT, Claude]
- **AI Prompting & Constraint Strategy:** Explain how you will constrain AI models to generate code (in Python or your chosen language) that adheres strictly to the protocol blueprint and FSM designed in Sprints 1 & 2.
- **Implementation Risk Management:** Detail your plan to leverage past programming experience and manage time to ensure code completion on schedule.

---

## 5. CML Multi-Subnet Topology & Wireshark Deployment Plan (Sprint 4 & 5 Deliverable)

> For now you can use the topology below. We may update this when we get to defining subnets.

### 5.1 Subnet & Router Design
- **Subnet A (Client 1):** `192.168.10.0/24` (Interface `Gi0/1` on Router R1)
- **Subnet B (Client 2):** `192.168.11.0/24` (Interface `Gi0/2` on Router R1)
- **Subnet C (Game Server):** `192.168.20.0/24` (Interface `Gi0/1` on Router R2)
- **Router Backbone:** `10.0.0.0/30` (Interface `Gi0/0` on R1 <-> `Gi0/0` on R2)

### 5.2 DHCP Pools & DNS Configuration Plan
- **Router R1 DHCP Pool 1 (`CLIENT1_POOL`):** Leases `192.168.10.10` - `192.168.10.50`, gateway `192.168.10.1`, DNS `10.0.0.2`.
- **Router R1 DHCP Pool 2 (`CLIENT2_POOL`):** Leases `192.168.11.10` - `192.168.11.50`, gateway `192.168.11.1`, DNS `10.0.0.2`.
- **Router R2 Authoritative DNS:** Configured with `ip dns server` and static host mapping `server.[yourlastname].edu` -> `192.168.20.100`.

### 5.3 Deployment Strategy & Wireshark Trace Capture
- **CML Deployment Strategy:** Deploy `server.py` onto Subnet C node (`192.168.20.100`) behind Router R2, and `client.py` onto Subnet A and Subnet B nodes behind Router R1.
- **Cisco Infrastructure Configuration:** Router R1 DHCP pools (`CLIENT1_POOL`, `CLIENT2_POOL`) and Router R2 authoritative DNS (`ip host server.[lastname].edu 192.168.20.100`).
- **Wireshark Trace Capture Plan:** Capture DHCP DORA exchange (`dhcp_negotiation.pcap`) and DNS query/response resolution (`dns_lookup.pcap`).
