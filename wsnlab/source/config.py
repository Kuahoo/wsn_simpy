# network properties
BROADCAST_NET_ADDR = 255
BROADCAST_NODE_ADDR = 255


# node properties
NODE_TX_RANGE = 100  # transmission range of nodes
NODE_ARRIVAL_MAX = 200  # max time to wake up


# simulation properties
SIM_NODE_COUNT = 40  # noce count in simulation
SIM_NODE_PLACING_CELL_SIZE = 75  # cell size to place one node
SIM_DURATION = 9000  # simulation Duration in seconds
SIM_TIME_SCALE = 0.000001  # The real time dureation of 1 second simualtion time
SIM_TERRAIN_SIZE = (1400, 1400)  # terrain size
SIM_TITLE = "Data Collection Tree"  # title of visualization window
SIM_VISUALIZATION = True  # visualization active
SCALE = 1  # scale factor for visualization


# application properties
HEARTH_BEAT_TIME_INTERVAL = 100
REPAIRING_METHOD = "FIND_ANOTHER_PARENT"  # 'ALL_ORPHAN', 'FIND_ANOTHER_PARENT'
EXPORT_CH_CSV_INTERVAL = 10  # simulation time units;
EXPORT_NEIGHBOR_CSV_INTERVAL = 10  # simulation time units;

PROCESSING_DELAY = 0.001  # 1ms per packet
TRANSMISSION_DELAY_PER_BYTE = 0.000032  # 32μs/byte @ 250kbps


# In config.py
MAX_CHILDREN_PER_CH = 15  # Already exists
PROMOTE_THRESHOLD = 3  # Already exists

# In config.py
TX_POWER_MIN = 50  # meters
TX_POWER_MAX = 150  # meters
TX_POWER_MODE = "uniform"  # or "per_cluster"
TX_POWER_PER_CLUSTER = {}  # {cluster_net_id: power_level}

# In config.py
PACKET_LOSS_RATIO = 0.0  # 0% to 100%

# In config.py
CLUSTER_OVERLAP_THRESHOLD = 80  # Increase from 60
ROUTER_BOUNDARY_DISTANCE = 100  # Min distance for boundary detection

# Failure Simulation
ENABLE_FAILURES = True
FAILURE_SCHEDULE = [
    (500, 5),   # (time, node_id)
    (1000, 12),
]
