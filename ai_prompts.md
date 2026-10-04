# AI Prompting Strategy, Restrictions, and Documentation


**Over the course of the project I will be adhering to a restrictive prompting strategy to create guard rails
for the generative AI model. This will ensure that code remains readable and maintainable by me, and that I
can fully understand the context of the codebase both in terms of the Battleship game logic and the 
networking socket and protocol implementation. Below I have outline some key guard rail strategies I will be using,
and plan to iterate and improve them over the project as needed.**

### Prompting Strategy and Example Prompts
**1. Restrict the amount of code generated in one prompt/instance and maintain modularity.** 
    When prompting the model for code I will do it at a fairly granular level, focusing on single functions or classes at a time (if they're sufficiently small), rather than making large scale prompts that involve creation of multiple classes at once with different dependancies between them that can be hard to track. There will not be any "one-shot prompting" attemps at trying to generate a functional networking project out of a detailed one-shot prompt, which is open to hallucinations and buggy, unclear framework.

```text
"Only generate the BoardPlacement() helper function for checking valid ship coordinates. There should be no additional dependencies with any other function or class other than BoardPlacement() to ensure tight coupling."
```

**2. Put constrains on dependancies and Python library usage**

I will force the AI model to stick to standard Python libraries for networking to keep the protocols ones that are commonly used, simple, and understood. This will ensure the code remains low level and fulfills the networking protocols required of the project, rather than using a higher level third party library tool that could potentially circumvent required network protocols while looking functional.

```text
"Must write code using only Python standard socket and json libraries. Do not using any higher level third party frameworks."
```

**3. Constraints on syntax and stream parsing**

Ensure important constraints on Python syntax, especially regarding things like parsing packets over the network data stream with the proper delimiter ('\n') and keeping syntax clean and maintainable.

```text
"Use simple, readable syntax for the list creation in this function so it's easy to follow and debug. List comprehension synatx is ok as long as it's intuitive and easy to understand."
```

```text
"Every network output from this data stream function must be a JSON string terminated with a newline character \n. Every network input parser must accept raw bytes, handling partial data reads, and parse for \n to differentiate the different JSON payloads."
```

**4. Ensure strict constraints on adhering to the project FSM Logic**

Do not let the AI model deviate from the logic outlined in the FSM, potentially inventing additional states or flow of control. Each class should help create and maintain a predefined FSM state (or at least be a support/helper class to a state in some fashion) rather than deviating into new logic.

```text
"Create the function serialize_message(msg_type: str, player_id: str, payload: dict) -> bytes. You must check that the msg_type is one of the 9 allowed protocol types and never deviate from those valid states when handling and modifying the JSON payloads."
```

**5. Limit broad try-catch blocks and ensure that important exceptions are being caught and handled explicitly**

AI models tend to use fairly broad exception handling when allowed. For the networking logic of the project we want very specific exception handling for important errors such as a TCP EOF.

```text
"You must explicitly catch ConnectionResetError and BrokenPipeErrors indicating an abrupt socket connection on the network. If sock.recv() returns b"" in the data stream log a TCP EOF and return a DISCONNECT message to the caller. Do not use generic except: blocks for any network protocol error handling."
```
