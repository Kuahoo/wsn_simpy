# WSN Data Collection Tree Simulator

A Python-based discrete-event simulator for Wireless Sensor Networks (WSN). This project models a robust Data Collection Tree topology featuring dynamic clustering, fault tolerance, packet loss simulation, and real-time visualization. It is built using SimPy for event scheduling and Tkinter (via topovis) for visualization.

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

## Network Addressing Format

### Address Types Overview

| Address Type | Class | Format | Purpose | Example | Who Has It |
|--------------|-------|---------|---------|---------|------------|
| **Network Address** | `wsn.Addr` | `net_addr.node_addr` | Routing packets in the network | `5.12` | All registered nodes |
| **GUI Address** | `GUIAddr` | `major.minor` | Visual display identifier | `2.7` | All registered nodes |
| **Cluster Head Address** | `wsn.Addr` | `net_addr.254` | Identifies cluster head | `5.254` | All cluster heads |

---

### Address Components

#### wsn.Addr (Network Address)

| Component | Type | Description | Range | Set By |
|-----------|------|-------------|-------|--------|
| `net_addr` | int | Network ID / Cluster ID | 0 - Node Count | ROOT (for CHs), Parent (for members) |
| `node_addr` | int | Node ID within network | 0-253 (members), 254 (CH) | Node's own ID or 254 for CH |

#### GUIAddr (Visual Address)

| Component | Type | Description | Range | Set By |
|-----------|------|-------------|-------|--------|
| `major` | int | Visual network ID | 0 - Network Count | Parent's visual network |
| `minor` | int | Sequential index | 0 (CH), 1+ (members) | Parent assigns sequentially |

---

### Role-Based Addressing

| Role | `addr` | `gui_addr` | `ch_addr` | Example |
|------|--------|------------|-----------|---------|
| **ROOT** | `0.254` | `0.0` | `0.254` | Root at node 0 |
| **CLUSTER_HEAD** | `net_id.254` | `visual_net.0` | `net_id.254` | CH at node 5: addr=`5.254`, gui=`2.0` |
| **REGISTERED** | `parent_net.node_id` | `visual_net.seq_idx` | `parent_net.254` | Member node 12 in net 5: addr=`5.12`, gui=`2.3` |
| **ROUTER** | `parent_net.node_id` | `visual_net.seq_idx` | `parent_net.254` | Router node 17 in net 5: addr=`5.17`, gui=`2.8` |
| **UNREGISTERED** | `None` | `None` | `None` | Orphaned node |
| **UNDISCOVERED** | `None` | `None` | `None` | Not yet discovered |

---

### Example Network Hierarchy

```text
ROOT (0)
├─ addr: 0.254, gui: 0.0
│
├─ CH (5)
│  ├─ addr: 5.254, gui: 1.0
│  ├─ Member (12): addr: 5.12, gui: 1.1
│  ├─ Member (15): addr: 5.15, gui: 1.2
│  └─ Router (17): addr: 5.17, gui: 1.3
│
└─ CH (8)
   ├─ addr: 8.254, gui: 2.0
   ├─ Member (10): addr: 8.10, gui: 2.1
   └─ Member (22): addr: 8.22, gui: 2.2
```

## Project Structure
- data_collection_tree.py: Main file. Contains the SensorNode logic (state machine), CSV export functions, and simulation setup.
- source/config.py: Central configuration file for simulation parameters (node count, timing, failures, etc.).
- source/wsnlab.py: Core simulator engine wrapper around simpy. Handles node creation and basic message passing.
- source/wsnlab_vis.py: Visualization wrapper connecting the simulation logic to the GUI.

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

   | Parameter | Description |
   |-----------|-------------|
   |SIM_NODE_COUNT | Total number of nodes to generate. |
   | PACKET_LOSS_RATIO | Probability (0.0 - 1.0) of a packet being dropped. |
   | MAX_CHILDREN_PER_CH | Capacity limit for Cluster Heads. |
   | ENABLE_FAILURES | Toggle failure injection logic. | 
   | FAILURE_SCHEDULE | List of tuples (time, node_id) to kill specific nodes. |
   | SIM_VISUALIZATION | Set to False for headless mode (faster). |

## Simulation Logic & Roles
The nodes use a Finite State Machine (FSM) to determine their behavior. The color of the node in the visualization indicates its current role:
   - Undiscovered (White): Initial state. Listening for beacons.
   - Unregistered (Yellow): Orphaned or initializing. Actively probing for a parent.
   - Registered (Green): Successfully joined a cluster. Acts as a leaf sensor node.
   - Cluster Head (Blue): Manages a local network/cluster. Aggregates data from children.
   - Router (Purple): Connects different clusters.
   - Root (Black): The central sink node (Node 0). Collects all data.
   - Dead (Black 'X'): Node has failed/died.

## Output Metrics (CSV)
After the simulation finishes (or periodically during execution), the following CSV files are generated for analysis:
   - packet_metrics.csv: End-to-end delay, hop counts, and routing methods for every packet.
   - packet_paths.csv: The exact path (sequence of node IDs) every packet took.
   - packet_loss.csv: Statistics on sent vs. dropped packets per node.
   - failure_events.csv: Log of when nodes died and the type of failure.
   - recovery_metrics.csv: Time taken for orphaned nodes to rejoin the network.
   - role_events.csv: Log of role changes within the network. 
   - orphan_metrics.csv: Global count of orphaned nodes over time.
   - cluster_events.csv: Log of cluster formation, member joins
   - node_distances.csv: Pairwise physical distances between nodes.
   - neighbor_distances.csv: Pairwise physical distances node and neighbor.
   - clusterhead_distances.csv: Pairwise physical distances between clusterheads.
   - join_times: Log of join times for nodes.

## Failure & Recovery Testing
To test the robustness of the algorithm:
- Open config.py.
- Set ENABLE_FAILURES = True.
- Define a schedule, e.g., FAILURE_SCHEDULE = [(500, 5)] (Node 5 dies at t=500).
- Run the simulation.
