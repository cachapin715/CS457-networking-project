### Game State Machine (FSM) Design
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