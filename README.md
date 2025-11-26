# WSN Data Collection Tree Simulator

A Python-based discrete-event simulator for Wireless Sensor Networks (WSN). This project models a robust Data Collection Tree topology featuring dynamic clustering, fault tolerance, packet loss simulation, and real-time visualization.

It is built using SimPy for event scheduling and Tkinter (via topovis) for visualization.

## Features
- Dynamic Topology Construction: Automatically forms a network tree consisting of a Root, Cluster Heads (CH), Routers, and Sensor Nodes.
- Self-Healing & Fault Tolerance: Nodes detect parent failures, become orphans, and autonomously seek new parents or restructure the network (e.g., promoting Routers to Cluster Heads).
- Realistic Network Simulation:
- Processing delays and transmission delays based on packet size.
- Configurable packet loss ratios.
- Collision and channel capacity simulation.
- Failure Injection: ability to schedule specific node deaths at specific times to test recovery logic.
- Rich Data Export: Generates detailed CSV logs for analysis, including packet latency, path tracking, energy/state changes, and failure impact.
- Real-time Visualization: Color-coded nodes representing different roles and states.

## Project Structure
- data_collection_tree.py: Main file. Contains the SensorNode logic (state machine), CSV export functions, and simulation setup.
- config.py: Central configuration file for simulation parameters (node count, timing, failures, etc.).
- wsnlab.py: Core simulator engine wrapper around simpy. Handles node creation and basic message passing.
- wsnlab_vis.py: Visualization wrapper connecting the simulation logic to the GUI.

##  Installation & Requirements
### Prerequisites
- Python 3.8+
- simpy
- tkinter (Usually included with Python)

## Setup
Clone the repository.
Install dependencies:
   - pip install simpy
(Note: The project requires topovis for visualization. Ensure the topovis library folder is present in your source directory or python path.)

## Usage
Run the simulation by executing the main script:
   - python data_collection_tree.py
Upon running, a GUI window will appear showing the nodes being placed and the network forming.

## Configuration (config.py)
You can customize the behavior of the simulation by editing source/config.py.
    Parameter                   Description
    SIM_NODE_COUNT              Total number of nodes to generate.
    PACKET_LOSS_RATIO           Probability (0.0 - 1.0) of a packet being dropped.
    MAX_CHILDREN_PER_CH         Capacity limit for Cluster Heads.
    ENABLE_FAILURES             Toggle failure injection logic.
    FAILURE_SCHEDULE            List of tuples (time, node_id) to kill specific nodes.
    SIM_VISUALIZATION           Set to False for headless mode (faster).

## Simulation Logic & Roles
The nodes use a Finite State Machine (FSM) to determine their behavior. The color of the node in the visualization indicates its current role:
   - Undiscovered (White): Initial state. Listening for beacons.
   - Unregistered (Yellow): Orphaned or initializing. Actively probing for a parent.
   - Registered (Green): Successfully joined a cluster. Acts as a leaf sensor node.
   - Cluster Head (Blue): Manages a local network/cluster. Aggregates data from children.
   - Router (Purple): Connects different clusters.
   - Root (Black): The central sink node (Node 0). Collects all data.
   - Dead (Black 'X'): Node has failed/died.

## Addressing
Nodes use a Major.Minor visual addressing scheme (e.g., 2.5), where:
   - Major: The Network ID (usually the Cluster Head's ID).
   - Minor: The Node's unique ID or assigned index.

## Output Metrics (CSV)
After the simulation finishes (or periodically during execution), the following CSV files are generated for analysis:
   - packet_metrics.csv: End-to-end delay, hop counts, and routing methods for every packet.
   - packet_paths.csv: The exact path (sequence of node IDs) every packet took.
   - packet_loss.csv: Statistics on sent vs. dropped packets per node.
   - failure_events.csv: Log of when nodes died and the type of failure.
   - recovery_metrics.csv: Time taken for orphaned nodes to rejoin the network.
   - orphan_metrics.csv: Global count of orphaned nodes over time.
   - cluster_events.csv: Log of cluster formation, member joins, and role changes.
   - node_distances.csv: Pairwise physical distances between nodes.

## Failure & Recovery Testing
To test the robustness of the algorithm:
Open config.py.
Set ENABLE_FAILURES = True.
Define a schedule, e.g., FAILURE_SCHEDULE = [(500, 5)] (Node 5 dies at t=500).
Run the simulation.

