import csv
from collections import Counter
from source import config
import math
from source import wsnlab_vis as wsn
import random
from enum import Enum
import sys

sys.path.insert(1, ".")

class SimulationContext:
    """Centralized simulation state"""
    def __init__(self):
        self.node_positions = {}
        self.orphan_count = 0
        self.orphan_history = []
        self.failure_events = []
        self.current_failure_id = 0
        self.role_counts = Counter()
    
    def increment_orphan_count(self, time):
        self.orphan_count += 1
        self.orphan_history.append((time, self.orphan_count))
    
    def decrement_orphan_count(self, time):
        self.orphan_count = max(0, self.orphan_count - 1)
        self.orphan_history.append((time, self.orphan_count))
    
    def register_failure(self, time, node_id, role_at_death):
        self.current_failure_id += 1
        self.failure_events.append({
            'failure_id': self.current_failure_id,
            'time': time,
            'node_id': node_id,
            'event_type': 'NODE_DIED',
            'role_at_death': role_at_death,
        })
        return self.current_failure_id
    
    def register_recovery(self, failure_id, time, node_id, new_role, recovery_duration):
        self.failure_events.append({
            'failure_id': failure_id,
            'time': time,
            'node_id': node_id,
            'event_type': 'NODE_RECOVERED',
            'new_role': new_role,
            'recovery_duration': recovery_duration,
        })

SIMULATION_CONTEXT = None  # Global instance placeholder

# --- tracking containers ---
# ROLE_COUNTS moved to SimulationContext

Roles = Enum("Roles", "UNDISCOVERED UNREGISTERED ROOT REGISTERED CLUSTER_HEAD ROUTER")

# --- GLOBAL METRICS ---
# ORPHAN_COUNT, ORPHAN_HISTORY, FAILURE_EVENTS, CURRENT_FAILURE_ID moved to SimulationContext

PACKET_LOSS_RATIO = getattr(config, "PACKET_LOSS_RATIO", 0.0)
TRANSMISSION_DELAY_PER_BYTE = config.TRANSMISSION_DELAY_PER_BYTE  # 0.1 ms per byte
PROCESSING_DELAY = config.PROCESSING_DELAY  # 5ms fixed processing time

# --- FAILURE CONFIGURATION ---
ENABLE_FAILURES = True
# Format: (time, node_id) - Schedule specific nodes to die
FAILURE_SCHEDULE = config.FAILURE_SCHEDULE
###########################################################


class GUIAddr:
    def __init__(self, major, minor):
        self.major = major
        self.minor = minor

    def __repr__(self):
        return f"{self.major}.{self.minor}"

    def __eq__(self, other):
        if not isinstance(other, GUIAddr):
            return False
        return self.major == other.major and self.minor == other.minor


###########################################################


class SensorNode(wsn.Node):
    """SensorNode class is inherited from Node class in wsnlab.py."""

    ###################
    def init(self):
        """Initialization of node."""
        global SIMULATION_CONTEXT
        if SIMULATION_CONTEXT is None:
            SIMULATION_CONTEXT = SimulationContext()
        self.sim_context = SIMULATION_CONTEXT

        self.scene.nodecolor(self.id, 1, 1, 1)  # sets self color to white
        self.sleep()
        self.addr = None
        self.ch_addr = None
        self.parent_gui = None
        self.root_addr = None

        # New attributes
        self.gui_addr = None
        self.root_id = None
        self.is_dead = False
        self.start_time = self.now
        self.join_duration = None

        # --- sent packets counter ---
        self.send_counter = 0

        # --- Recovery Metrics ---
        self.role_events = []
        self.orphan_time = None

        # --- Neighbor Discovery Metrics ---
        self.neighbor_discovery_events = []
        self.neighbor_first_seen = {}  # {gui: first_heard_time}

        # --- Joining Network Metrics ---
        self.cluster_events = []

        self.packets_sent = 0
        self.packets_dropped = 0

        self.router_forwards = 0

        # Sequential Addressing State
        self.next_gui_index = 1
        self.next_network_index = 1

        # Cooldown tracking
        self.last_demotion_time = None
        self.demotion_history = []
        self.cooldown_history_window = 120
        self.demotion_count = 0

        # Cooldown tiers
        self.cooldown_short = 10
        self.cooldown_medium = 20
        self.cooldown_long = 40

        self.demotion_parent_gui = None
        self.demotion_location = None
        self.cooldown_distance_threshold = 100

        self.network_request_sent = False

        # Network request retry tracking
        self.network_request_pending = False
        self.network_request_attempts = 0
        self.max_network_request_attempts = 5
        self._router_upgrade_pending = False  # Flag for router upgrade process

        # Neighbor timeout tracking
        self.neighbor_timeout = 250

        # Join retry tracking
        self.last_join_parent = None
        self.join_retry_count = 0
        self.max_join_retries = 3

        #  remove duplicate requests tracker
        self.processed_network_requests = {}
        self.request_dedup_window = 10
        self.duplicate_requests_blocked = 0

        # re-probe for recovery
        self.unregistered_since = None
        self.forced_reprobe_threshold = 60

        # Periodic status logging
        self.last_status_log = 0
        self.status_log_interval = 30

        # Periodic neighbor re-discovery
        self.unregistered_probe_interval = 10
        self.last_unregistered_probe = 0

        # Fallback parent response tracking
        self.fallback_response_delay = 3
        self.pending_join_replies = {}

        # Aggressive candidate expansion
        self.min_candidate_threshold = 2
        self.expansion_attempted = False

        self.set_role(Roles.UNDISCOVERED)
        self.is_root_eligible = True if self.id == ROOT_ID else False
        self.c_probe = 0
        self.th_probe = 10
        self.hop_count = 99999
        self.neighbors_table = {}
        self.candidate_parents_table = []
        self.child_networks_table = {}
        self.members_table = []
        self.received_JR_guis = []
        self.packet_delays = []
        self.packet_paths = []

        # 2-hop mesh routing table
        # {dest_gui: {'next_hop': addr, 'hop_count': 2, 'last_updated': time}}
        self.routing_table = {}

        # Router upgrade queue
        self.pending_child_joins = []  # Queue for nodes trying to join when we're a ROUTER

        # Routing table maintenance
        self.routing_table_timeout = 60  # Remove stale 2-hop routes after 60s

        # Capacity management
        self.max_children = getattr(config, "MAX_CHILDREN_PER_CH", 15)
        self.promote_threshold = 3

        self.current_children = 0
        self.capacity_reached = False
        self.parent_at_capacity = False
        self.last_forward_time = 0

    ###################
    def estimate_packet_size(self, pck):
        """Estimate packet size in bytes based on string representation."""
        return len(str(pck))

    def send(self, pck):
        """
        Overridden send method to implement:
        1. Packet Loss
        2. Processing & Transmission Delay
        """
        self.packets_sent += 1

        # Validate destination exists before processing
        # This prevents AttributeError: 'NoneType' object has no attribute 'is_equal'
        if "dest" not in pck or pck["dest"] is None:
            self.packets_dropped += 1
            return

        # Packet Loss
        if random.random() < PACKET_LOSS_RATIO:
            self.packets_dropped += 1
            return  # Packet Lost

        # Delay calculation
        packet_size = self.estimate_packet_size(pck)
        tx_time = TRANSMISSION_DELAY_PER_BYTE * packet_size
        processing_time = PROCESSING_DELAY
        total_delay = tx_time + processing_time

        # Schedule the actual send
        timer_name = f"TIMER_DELAYED_SEND_{self.send_counter}"
        self.set_timer(timer_name, total_delay, pck=pck)
        self.send_counter += 1

    # role event log
    ###################
    def log_role_event(self, event_type, details):
        """Log role-related events for later export."""
        event = {
            "time": self.now,
            "node_id": self.id,
            "event_type": event_type,
            "details": details,
        }
        self.role_events.append(event)

    # FAILURE & RECOVERY LOGIC
    ###################
    def detect_parent_failure(self):
        """Called when parent dies - triggers recovery"""
        if self.role == Roles.ROOT:
            return  # Root doesn't have a parent failure

        self.log_role_event("PARENT_FAILED", {"parent_gui": self.parent_gui})
        self.become_unregistered()

    def check_parent_alive(self):
        """Returns False if parent timed out"""
        if self.role == Roles.ROOT or self.parent_gui is None:
            return True

        if self.parent_gui not in self.neighbors_table:
            # Parent disappeared from table
            return False

        last_heard = self.neighbors_table[self.parent_gui].get("last_heard", 0)
        # Reuse existing neighbor timeout threshold
        return (self.now - last_heard) < self.neighbor_timeout

##############################
    def die(self):
        """Simulates node death"""
        self.is_dead = True
        self.kill_all_timers()

        # Global Failure Tracking via Context
        failure_id = self.sim_context.register_failure(self.now, self.id, self.role.name)
        self.current_failure_id = failure_id  # Track which failure affected this node

        # Trigger cascading failures to children
        if "sim" in globals():
            for child_gui in self.members_table[:]:
                child_node = next((n for n in sim.nodes if n.id == child_gui), None)
                if child_node and not child_node.is_dead:
                    child_node.detect_parent_failure()

        # Log death as an orphan event if applicable
        if self.role != Roles.UNDISCOVERED:
            self.log_role_event("DIED", {"last_role": self.role.name})

            # Decrement orphan count if it was one
            if self.role == Roles.UNREGISTERED:
                self.sim_context.decrement_orphan_count(self.now)
                
        self.erase_parent()

        self.set_role(Roles.UNDISCOVERED, recolor=False)
        self.scene.nodecolor(self.id, 0, 0, 0)  # Black
        self.set_label("X")
        self.log(f"Node {self.id} has died")

#######################
# Override erase_parent for safety check
    def erase_parent(self):
        """Override erase_parent to safely handle missing links."""
        if self.parent_gui is not None:
            try:
                self.scene.dellink(self.parent_gui, self.id, "parent")
            except (KeyError, ValueError):
                # Link doesn't exist in visualization - this is OK
                # Can happen if link was never drawn or already removed
                pass
        self.parent_gui = None

    ###################
    def run(self):
        """Setting the arrival timer to wake up after firing."""
        self.set_timer("TIMER_ARRIVAL", self.arrival)

    ###################
    def set_role(self, new_role, *, recolor=True):
        """Central place to switch roles, keep tallies, and (optionally) recolor."""
        old_role = getattr(self, "role", None)

        # Log role changes
        if old_role != new_role and old_role is not None:
            self.log_role_event(
                "ROLE_CHANGE", {"from": old_role.name, "to": new_role.name}
            )

        if old_role is not None:
            self.sim_context.role_counts[old_role] -= 1
            if self.sim_context.role_counts[old_role] <= 0:
                self.sim_context.role_counts.pop(old_role, None)
        self.sim_context.role_counts[new_role] += 1
        self.role = new_role

        if recolor:
            if new_role == Roles.UNDISCOVERED:
                self.scene.nodecolor(self.id, 1, 1, 1)
            elif new_role == Roles.UNREGISTERED:
                self.scene.nodecolor(self.id, 1, 1, 0)
            elif new_role == Roles.REGISTERED:
                self.scene.nodecolor(self.id, 0, 1, 0)  # Green
            elif new_role == Roles.CLUSTER_HEAD:
                self.scene.nodecolor(self.id, 0, 0, 1)
                self.draw_tx_range()
            elif new_role == Roles.ROUTER:
                self.scene.nodecolor(self.id, 0.5, 0, 0.5)  # Purple
            elif new_role == Roles.ROOT:
                self.scene.nodecolor(self.id, 0, 0, 0)
                self.set_timer("TIMER_EXPORT_CH_CSV", config.EXPORT_CH_CSV_INTERVAL)
                self.set_timer(
                    "TIMER_EXPORT_NEIGHBOR_CSV", config.EXPORT_NEIGHBOR_CSV_INTERVAL
                )

    # --- ROUTER FORMATION METHODS ---
    def is_at_cluster_boundary(self):
        """
        Enhanced boundary detection:
        - Check for multiple CHs from different networks
        - Check physical proximity to multiple CHs
        """
        ch_by_network = {}  # {net_addr: distance}

        for gui, info in self.neighbors_table.items():
            if info.get("role") == Roles.CLUSTER_HEAD:
                ch_addr = info.get("ch_addr")
                if ch_addr:
                    dist = info.get("distance", 999)
                    net = ch_addr.net_addr

                    # Track closest CH per network
                    if net not in ch_by_network or dist < ch_by_network[net]:
                        ch_by_network[net] = dist

        # At boundary if we hear 2+ different networks
        if len(ch_by_network) >= 2:
            return True

        # Also check if we hear same network from multiple directions
        ch_same_network = [
            gui
            for gui, info in self.neighbors_table.items()
            if info.get("role") == Roles.CLUSTER_HEAD
            and info.get("ch_addr")
            and info.get("ch_addr").net_addr
            == (self.ch_addr.net_addr if self.ch_addr else None)
        ]

        if len(ch_same_network) >= 2:
            # Check if they're physically separated
            if self.id in self.sim_context.node_positions:
                my_x, my_y = self.sim_context.node_positions[self.id]
                ch_positions = []
                for ch_gui in ch_same_network:
                    if ch_gui in self.sim_context.node_positions:
                        ch_x, ch_y = self.sim_context.node_positions[ch_gui]
                        ch_positions.append((ch_x, ch_y))

                # If CHs are far apart, we're at boundary
                if len(ch_positions) >= 2:
                    dist_between_chs = math.hypot(
                        ch_positions[0][0] - ch_positions[1][0],
                        ch_positions[0][1] - ch_positions[1][1],
                    )
                    if dist_between_chs > 100:  # Tunable
                        return True

        return False

    def should_demote_router(self):
        """Check if ROUTER should demote to REGISTERED"""
        if self.role != Roles.ROUTER:
            return False

        # Check if still at boundary
        if not self.is_at_cluster_boundary():
            # Check if we have any active role
            has_children = len(self.members_table) > 0
            has_pending = len(self.pending_child_joins) > 0

            # Check if we've forwarded packets recently
            recent_forward = (self.now - self.last_forward_time) < 30

            if not has_children and not has_pending and not recent_forward:
                return True

        return False

    def select_best_boundary_parent(self):
        """When at cluster boundary, select the best parent cluster to belong to."""
        if not self.is_at_cluster_boundary():
            return None, None

        ch_candidates = []
        for gui, info in self.neighbors_table.items():
            if info.get("role") == Roles.CLUSTER_HEAD:
                hop_count = info.get("hop_count", 99999)
                ch_addr = info.get("ch_addr")
                if ch_addr:
                    ch_candidates.append(
                        {"gui": gui, "ch_addr": ch_addr, "hop_count": hop_count}
                    )

        if not ch_candidates:
            return None, None

        # Select CH with minimum hop count
        best = min(ch_candidates, key=lambda x: x["hop_count"])
        return best["gui"], best["ch_addr"]

    def validate_address_consistency(self):
        """Verify that addr, ch_addr, and gui_addr are consistent with parent."""
        if self.role in [Roles.UNDISCOVERED, Roles.UNREGISTERED, Roles.ROOT]:
            return True

        if self.parent_gui not in self.neighbors_table:
            return False  # Parent disappeared

        parent_info = self.neighbors_table[self.parent_gui]
        parent_ch = parent_info.get("ch_addr")

        if not parent_ch:
            return False

        # Check if our network addresses match parent's cluster
        if self.ch_addr and self.ch_addr.net_addr != parent_ch.net_addr:
            return False

        if self.addr and self.addr.net_addr != parent_ch.net_addr:
            return False

        # Check against parent's visual major ID if available
        if self.gui_addr:
            target_major = parent_ch.net_addr  # Default fallback

            if parent_info.get("gui_addr"):
                target_major = parent_info["gui_addr"].major

            if self.gui_addr.major != target_major:
                return False

        return True

    def become_router_from_ch(self):
        """Demote from CLUSTER_HEAD to ROUTER (soft demotion to prevent overlap)"""
        # Find parent (closest non-overlapping CH)
        best_parent_gui = None
        min_dist = 999999

        for gui, info in self.neighbors_table.items():
            if info.get("role") == Roles.CLUSTER_HEAD:
                dist = info.get("distance", 999)
                if dist >= 60 and dist < min_dist:  # Not overlapping
                    min_dist = dist
                    best_parent_gui = gui

        if best_parent_gui and best_parent_gui in self.neighbors_table:
            parent_info = self.neighbors_table[best_parent_gui]
            parent_addr = parent_info.get("ch_addr")

            if parent_addr:
                # Save old GUI for reference
                old_gui = self.gui_addr

                # Transition to ROUTER
                self.set_role(Roles.ROUTER)
                self.parent_gui = best_parent_gui

                # Log Router transition
                self.cluster_events.append(
                    {
                        "time": self.now,
                        "event_type": "BECAME_ROUTER",
                        "node_id": self.id,
                        "previous_role": "CLUSTER_HEAD",
                        "parent_gui": self.parent_gui,
                    }
                )

                # Update our address to parent's network
                self.addr = wsn.Addr(parent_addr.net_addr, self.id)
                self.ch_addr = parent_addr

                parent_gui_major = parent_addr.net_addr
                parent_gui_addr = parent_info.get("gui_addr")
                if parent_gui_addr:
                    parent_gui_major = parent_gui_addr.major

                if old_gui:
                    self.gui_addr = GUIAddr(parent_gui_major, old_gui.minor)
                else:
                    self.gui_addr = GUIAddr(parent_gui_major, self.id)

                self.set_label(f"{self.gui_addr}")
                self.send_heart_beat()

                self.members_table = []
                self.current_children = 0
                self.capacity_reached = False
        else:
            self.become_unregistered()

    def should_promote_to_ch(self):
        """Determine if REGISTERED node should promote to CH."""
        if (
            not hasattr(self, "pending_child_joins")
            or len(self.pending_child_joins) == 0
        ):
            return False

        if self.is_at_cluster_boundary():
            return False  # Should become ROUTER, not CH

        if self.parent_at_capacity:
            return True

        if len(self.pending_child_joins) >= self.promote_threshold:
            return True

        return False

    # -------------------------------

    ###################
    def become_root(self):
        self.set_role(Roles.ROOT)
        self.addr = wsn.Addr(self.id, 254)
        self.gui_addr = GUIAddr(0, 0)  # Root is 0.0
        self.set_label(str(self.gui_addr))

        self.next_gui_index = 1  # Reset child counter
        self.next_network_index = 1  # Reset network counter
        self.duplicate_requests_blocked = 0

        self.current_children = 0
        self.capacity_reached = False

        self.ch_addr = self.addr
        self.root_addr = self.addr
        self.root_id = self.id
        self.hop_count = 0
        self.set_timer("TIMER_HEART_BEAT", config.HEARTH_BEAT_TIME_INTERVAL)

    def become_cluster_head(self, net_id, assigned_net_index=None):
        # Log Recovery Complete Logic
        if self.orphan_time is not None:
            recovery_time = self.now - self.orphan_time
            self.log_role_event(
                "RECOVERY_COMPLETE",
                {
                    "orphan_time": self.orphan_time,
                    "recovery_duration": recovery_time,
                    "role": "CLUSTER_HEAD",
                },
            )
            self.sim_context.decrement_orphan_count(self.now)

            # Log Per-failure recovery
            if hasattr(self, "current_failure_id"):
                self.sim_context.register_recovery(
                    self.current_failure_id, 
                    self.now, 
                    self.id, 
                    self.role.name, 
                    recovery_time
                )
                delattr(self, "current_failure_id")

            self.orphan_time = None

        # Clear pending request tracking
        self.network_request_pending = False
        self.network_request_attempts = 0
        self.network_request_sent = False
        self._router_upgrade_pending = False
        self.kill_timer("TIMER_NETWORK_REQUEST_RETRY")

        self.set_role(Roles.CLUSTER_HEAD)

        self.addr = wsn.Addr(net_id, 254)

        visual_net = assigned_net_index if assigned_net_index is not None else net_id
        self.gui_addr = GUIAddr(visual_net, 0)
        self.set_label(str(self.gui_addr))

        # Log Cluster Formation
        self.cluster_events.append(
            {
                "time": self.now,
                "event_type": "CLUSTER_FORMED",
                "node_id": self.id,
                "net_id": net_id,
                "visual_net": visual_net,
                "parent_gui": self.parent_gui,
            }
        )

        self.next_gui_index = 1
        self.ch_addr = self.addr
        self.scene.nodecolor(self.id, 0, 0, 1)  # BLUE

        self.current_children = 0
        self.capacity_reached = False

        # --- PROCESS QUEUED JOIN REQUESTS IMMEDIATELY ---
        if hasattr(self, "pending_child_joins") and len(self.pending_child_joins) > 0:
            for req_pck in self.pending_child_joins:
                requestor_gui = req_pck["gui"]

                idx = self.next_gui_index
                self.next_gui_index += 1

                visual_net = self.gui_addr.major

                self.send_join_reply(
                    requestor_gui,
                    wsn.Addr(self.ch_addr.net_addr, requestor_gui),
                    assigned_gui_index=idx,
                    visual_net_id=visual_net,
                )

            self.pending_child_joins = []
            self.send_neighbor_table_update()
        # ------------------------------------

        self.send_network_update()
        self.send_heart_beat()

        try:
            write_clusterhead_distances_csv("clusterhead_distances.csv")
        except Exception:
            pass

    def become_registered(
        self, parent_addr, assigned_gui_index=None, visual_net_id=None
    ):
        # Log Recovery Complete Event
        if self.orphan_time is not None:
            recovery_time = self.now - self.orphan_time
            self.log_role_event(
                "RECOVERY_COMPLETE",
                {
                    "orphan_time": self.orphan_time,
                    "recovery_duration": recovery_time,
                    "role": "REGISTERED",
                },
            )
            self.sim_context.decrement_orphan_count(self.now)

            # Log failure event
            if hasattr(self, "current_failure_id"):
                self.sim_context.register_recovery(
                    self.current_failure_id,
                    self.now,
                    self.id,
                    self.role.name,
                    recovery_time
                )
                delattr(self, "current_failure_id")

            self.orphan_time = None

        # Stop periodic probing
        self.kill_timer("TIMER_UNREGISTERED_PROBE")

        # Reset retry tracking on successful join
        self.last_join_parent = None
        self.join_retry_count = 0
        self.expansion_attempted = False
        self.unregistered_since = None

        self.set_role(Roles.REGISTERED)
        self.addr = wsn.Addr(parent_addr.net_addr, self.id)

        # Set ch_addr to parent's cluster head
        self.ch_addr = wsn.Addr(parent_addr.net_addr, 254)

        major = visual_net_id if visual_net_id is not None else parent_addr.net_addr
        minor = assigned_gui_index if assigned_gui_index is not None else self.id

        self.gui_addr = GUIAddr(major, minor)
        self.set_label(str(self.gui_addr))

        if self.join_duration is None:
            self.join_duration = self.now - self.start_time

        self.set_timer("TIMER_HEART_BEAT", config.HEARTH_BEAT_TIME_INTERVAL)
        self.send_heart_beat()

    def become_router(self, parent_addr, assigned_gui_index=None, visual_net_id=None):
        # log role event recovery complete
        if self.orphan_time is not None:
            recovery_time = self.now - self.orphan_time
            self.log_role_event(
                "RECOVERY_COMPLETE",
                {
                    "orphan_time": self.orphan_time,
                    "recovery_duration": recovery_time,
                    "role": "ROUTER",
                },
            )
            self.sim_context.decrement_orphan_count(self.now)

            # Log failure event
            if hasattr(self, "current_failure_id"):
                self.sim_context.register_recovery(
                    self.current_failure_id,
                    self.now,
                    self.id,
                    self.role.name,
                    recovery_time
                )
                delattr(self, "current_failure_id")

            self.orphan_time = None

        # Stop periodic probing
        self.kill_timer("TIMER_UNREGISTERED_PROBE")

        # Reset retry tracking on successful join
        self.last_join_parent = None
        self.join_retry_count = 0
        self.expansion_attempted = False
        self.unregistered_since = None

        self.pending_child_joins = []

        self.set_role(Roles.ROUTER)
        self.addr = wsn.Addr(parent_addr.net_addr, self.id)

        # Log Router transition
        self.cluster_events.append(
            {
                "time": self.now,
                "event_type": "BECAME_ROUTER",
                "node_id": self.id,
                # Could be passed in, but logic usually implies upgrade or join
                "previous_role": "UNKNOWN",
                "parent_gui": self.parent_gui,
            }
        )

        # Set ch_addr to parent's cluster head
        self.ch_addr = wsn.Addr(parent_addr.net_addr, 254)

        major = visual_net_id if visual_net_id is not None else parent_addr.net_addr
        minor = assigned_gui_index if assigned_gui_index is not None else self.id
        self.gui_addr = GUIAddr(major, minor)
        self.set_label(f"{self.gui_addr}")

        if self.join_duration is None:
            self.join_duration = self.now - self.start_time

        self.set_timer("TIMER_HEART_BEAT", config.HEARTH_BEAT_TIME_INTERVAL)
        self.send_heart_beat()

    ###################
    def become_unregistered(self):
        """Handles becoming unregistered"""
        was_cluster_head = self.role == Roles.CLUSTER_HEAD

        # Orphan Logic
        if self.role in [
            Roles.REGISTERED,
            Roles.CLUSTER_HEAD,
            Roles.ROUTER,
            Roles.UNDISCOVERED,
        ]:
            # UNDISCOVERED transitions to UNREGISTERED, effectively becoming an orphan of the system
            self.sim_context.increment_orphan_count(self.now)
            self.orphan_time = self.now

            self.log_role_event(
                "ORPHANED",
                {
                    "previous_role": self.role.name,
                    "parent_gui": self.parent_gui,
                    "was_cluster_head": was_cluster_head,
                },
            )

            # Propagate failure ID
            if self.parent_gui is not None:
                parent_node = next(
                    (n for n in sim.nodes if n.id == self.parent_gui), None
                )
                if parent_node and hasattr(parent_node, "current_failure_id"):
                    self.current_failure_id = parent_node.current_failure_id

        if self.role != Roles.UNDISCOVERED:
            self.kill_all_timers()
            if was_cluster_head:
                self.demotion_history.append(self.now)
                self.demotion_count += 1

                if self.last_demotion_time is not None:
                    self.demotion_parent_gui = self.parent_gui
                    if self.id in self.sim_context.node_positions:
                        self.demotion_location = self.sim_context.node_positions[self.id]

        self.scene.nodecolor(self.id, 1, 1, 0)
        self.set_label(str(self.id))
        self.erase_parent()
        self.addr = None
        self.ch_addr = None
        self.parent_gui = None
        self.root_addr = None
        self.gui_addr = None
        self.set_role(Roles.UNREGISTERED)

        # Reset request flag
        self.network_request_sent = False
        self.network_request_pending = False
        self.network_request_attempts = 0
        self.processed_network_requests = {}

        # Track when we became unregistered
        self.unregistered_since = self.now

        self.c_probe = 0
        self.th_probe = 10
        self.hop_count = 99999

        if not was_cluster_head:
            self.neighbors_table = {}

        self.candidate_parents_table = []
        self.child_networks_table = {}
        self.members_table = []
        self.received_JR_guis = []

        if was_cluster_head and len(self.neighbors_table) > 0:
            # Rebuild candidate parents from existing neighbors
            non_ch_parents = []
            ch_parents = []

            current_time = self.now
            valid_neighbor_threshold = 15

            for gui, info in self.neighbors_table.items():
                last_heard = info.get("last_heard", 0)

                if (current_time - last_heard) > valid_neighbor_threshold:
                    continue

                hop = info.get("hop_count", 99999)
                if hop >= 99999:
                    continue

                if info.get("role") == Roles.CLUSTER_HEAD:
                    ch_parents.append(gui)
                elif info.get("role") in [Roles.ROUTER, Roles.REGISTERED]:
                    non_ch_parents.append(gui)

            self.candidate_parents_table.extend(non_ch_parents)
            self.candidate_parents_table.extend(ch_parents)

            if len(self.candidate_parents_table) > 0:
                self.select_and_join()
                self.set_timer("TIMER_JOIN_REQUEST", 8)
            else:
                self.neighbors_table = {}
                self.send_probe()
                self.set_timer("TIMER_JOIN_REQUEST", 20)
        else:
            self.send_probe()
            self.set_timer("TIMER_JOIN_REQUEST", 20)

        self.last_unregistered_probe = self.now
        self.set_timer("TIMER_UNREGISTERED_PROBE", self.unregistered_probe_interval)

    ###################
    def update_neighbor(self, pck):
        pck["arrival_time"] = self.now
        pck["last_heard"] = self.now

        # compute distance between self and neighbor
        if pck["gui"] in self.sim_context.node_positions and self.id in self.sim_context.node_positions:
            x1, y1 = self.sim_context.node_positions[self.id]
            x2, y2 = self.sim_context.node_positions[pck["gui"]]
            pck["distance"] = math.hypot(x1 - x2, y1 - y2)
        self.neighbors_table[pck["gui"]] = pck

        if (
            pck["gui"] not in self.child_networks_table.keys()
            and pck["gui"] not in self.members_table
        ):
            if pck["gui"] not in self.candidate_parents_table:
                self.candidate_parents_table.append(pck["gui"])

    def cleanup_stale_neighbors(self):
        """Remove neighbors that haven't been heard from recently"""
        stale_guis = []

        for gui, info in self.neighbors_table.items():
            last_heard = info.get("last_heard", 0)

            # Check for None address to prevent errors downstream
            has_valid_addr = info.get("addr") is not None

            if (self.now - last_heard) > self.neighbor_timeout or not has_valid_addr:
                stale_guis.append(gui)

        for gui in stale_guis:
            del self.neighbors_table[gui]
            if gui in self.candidate_parents_table:
                self.candidate_parents_table.remove(gui)

    def process_routing_update(self, sender_gui, sender_addr, remote_neighbors):
        """Process a neighbor's neighbor table to build 2-hop mesh routes."""
        for remote_gui_str, info in remote_neighbors.items():
            try:
                remote_gui = int(remote_gui_str)
            except (ValueError, TypeError):
                continue

            if remote_gui == self.id:
                continue

            if remote_gui in self.neighbors_table:
                continue

            if remote_gui == self.id:
                continue

            should_update = False

            if remote_gui not in self.routing_table:
                should_update = True
            else:
                existing = self.routing_table[remote_gui]
                if (self.now - existing.get("last_updated", 0)) > 30:
                    should_update = True

            if should_update:
                self.routing_table[remote_gui] = {
                    "next_hop": sender_addr,
                    "hop_count": 2,
                    "last_updated": self.now,
                    "via_gui": sender_gui,
                }

    def cleanup_routing_table(self):
        """Remove stale 2-hop routes that haven't been updated recently"""
        stale_guis = []

        for gui, info in self.routing_table.items():
            last_updated = info.get("last_updated", 0)
            if (self.now - last_updated) > self.routing_table_timeout:
                stale_guis.append(gui)

        for gui in stale_guis:
            del self.routing_table[gui]

    def calculate_dynamic_cooldown(self):
        """Calculate cooldown based on demotion history"""
        if self.last_demotion_time is None:
            return 0

        current_time = self.now
        self.demotion_history = [
            t
            for t in self.demotion_history
            if (current_time - t) < self.cooldown_history_window
        ]

        recent_demotions = len(self.demotion_history)

        if recent_demotions == 0:
            cooldown = 0
        elif recent_demotions == 1:
            cooldown = self.cooldown_short
        elif recent_demotions == 2:
            cooldown = self.cooldown_medium
        else:
            cooldown = self.cooldown_long

        return cooldown

    ###################
    def select_and_join(self):
        self.cleanup_stale_neighbors()

        valid_candidates = []
        for gui in self.candidate_parents_table:
            if gui in self.neighbors_table:
                info = self.neighbors_table[gui]
                if info.get("hop_count", 99999) < 99999:
                    valid_candidates.append(gui)

        self.candidate_parents_table = valid_candidates

        if len(self.candidate_parents_table) < self.min_candidate_threshold:
            added = 0
            for gui in self.neighbors_table.keys():
                if gui not in self.candidate_parents_table:
                    neighbor_info = self.neighbors_table[gui]
                    if neighbor_info.get("parent_gui") != self.id:
                        self.candidate_parents_table.append(gui)
                        added += 1
            if added > 0:
                self.expansion_attempted = True

        min_hop = 99999
        min_hop_gui = 99999
        min_hop_candidates = []

        for gui in self.candidate_parents_table:
            if gui in self.neighbors_table:
                info = self.neighbors_table[gui]

                parent_claim_time = info.get("arrival_time", 0)
                if info.get("parent_gui") == self.id:
                    if (self.now - parent_claim_time) < 20:
                        continue

                if info["hop_count"] < min_hop:
                    min_hop = info["hop_count"]
                    min_hop_candidates = [gui]
                elif info["hop_count"] == min_hop:
                    min_hop_candidates.append(gui)

        if min_hop >= 99999:
            self.set_timer("TIMER_UNREGISTERED_PROBE", 5)
            self.set_timer("TIMER_JOIN_REQUEST", 10)
            return

        if len(min_hop_candidates) > 0:
            non_ch = [
                g
                for g in min_hop_candidates
                if self.neighbors_table[g].get("role") != Roles.CLUSTER_HEAD
            ]

            if len(non_ch) > 0:
                min_hop_gui = min(non_ch)
            else:
                min_hop_gui = min(min_hop_candidates)

        if min_hop_gui != 99999 and min_hop_gui in self.neighbors_table:
            selected_addr = self.neighbors_table[min_hop_gui]["source"]

            if self.last_join_parent == min_hop_gui:
                self.join_retry_count += 1

                if self.join_retry_count >= 5:
                    if min_hop_gui in self.candidate_parents_table:
                        self.candidate_parents_table.remove(min_hop_gui)
                        self.candidate_parents_table.append(min_hop_gui)

                    self.last_join_parent = None
                    self.join_retry_count = 0

                    self.select_and_join()
                    return
            else:
                self.last_join_parent = min_hop_gui
                self.join_retry_count = 1

            self.send_join_request(selected_addr, preferred_parent_gui=min_hop_gui)

            if self.join_retry_count >= 2:
                self.set_timer("TIMER_BROADCAST_JOIN", 3)

            self.set_timer("TIMER_JOIN_REQUEST", 5)

    ###################
    def send_probe(self):
        self.send(
            {"dest": wsn.BROADCAST_ADDR, "type": "PROBE", "creation_time": self.now}
        )

    ###################
    def send_heart_beat(self):
        """Sending heart beat message"""
        self.send(
            {
                "dest": wsn.BROADCAST_ADDR,
                "type": "HEART_BEAT",
                "source": self.ch_addr if self.ch_addr is not None else self.addr,
                "gui": self.id,
                "gui_addr": self.gui_addr,
                "role": self.role,
                "addr": self.addr,
                "ch_addr": self.ch_addr,
                "hop_count": self.hop_count,
                "parent_gui": self.parent_gui,
                "creation_time": self.now,
            }
        )

    def broadcast_capacity_status(self):
        """Broadcast capacity status to children"""
        self.send(
            {
                "dest": wsn.BROADCAST_ADDR,
                "type": "CAPACITY_STATUS",
                "source": self.addr,
                "gui": self.id,
                "at_capacity": self.capacity_reached,
                "creation_time": self.now,
            }
        )

    def send_delegate_to_ch(self, child_gui):
        """Signal a child node to become a cluster head"""
        if child_gui not in self.neighbors_table:
            return

        child_addr = self.neighbors_table[child_gui].get("addr")
        if not child_addr:
            return

        self.send(
            {
                "dest": child_addr,
                "type": "DELEGATE_TO_CH",
                "source": self.addr,
                "gui": self.id,
                "creation_time": self.now,
            }
        )

    ###################
    def send_join_request(self, dest, preferred_parent_gui=None):
        self.send(
            {
                "dest": wsn.BROADCAST_ADDR,
                "type": "JOIN_REQUEST",
                "gui": self.id,
                "preferred_parent": preferred_parent_gui,
                "creation_time": self.now,
            }
        )

    ###################
    def send_join_reply(
        self,
        gui,
        addr,
        assigned_gui_index=None,
        visual_net_id=None,
        assigned_role="REGISTERED",
    ):
        """Improved router assignment logic"""
        assigned_role = "REGISTERED"

        if visual_net_id is None:
            visual_net_id = self.gui_addr.major if self.gui_addr else self.addr.net_addr

        if gui in self.neighbors_table:
            requestor_info = self.neighbors_table[gui]
            requestor_neighbors = requestor_info.get("neighbors", {})

            if requestor_neighbors:
                ch_by_network = {}

                for n_gui, n_info in requestor_neighbors.items():
                    if n_info.get("role") == Roles.CLUSTER_HEAD:
                        ch_addr = n_info.get("ch_addr")
                        if ch_addr:
                            net = ch_addr.net_addr
                            if net not in ch_by_network:
                                ch_by_network[net] = []
                            ch_by_network[net].append(n_gui)

                if len(ch_by_network) >= 2:
                    assigned_role = "ROUTER"

                elif len(ch_by_network) == 1:
                    ch_list = list(ch_by_network.values())[0]
                    if len(ch_list) >= 2:
                        distances = []
                        for ch_gui in ch_list:
                            if ch_gui in self.sim_context.node_positions and gui in self.sim_context.node_positions:
                                x1, y1 = self.sim_context.node_positions[ch_gui]
                                x2, y2 = self.sim_context.node_positions[gui]
                                dist = math.hypot(x1 - x2, y1 - y2)
                                distances.append(dist)

                        if len(distances) >= 2:
                            avg_dist = sum(distances) / len(distances)
                            variance = sum(
                                (d - avg_dist) ** 2 for d in distances
                            ) / len(distances)
                            if variance < 400:
                                assigned_role = "ROUTER"

        self.send(
            {
                "dest": wsn.BROADCAST_ADDR,
                "type": "JOIN_REPLY",
                "source": self.ch_addr,
                "gui": self.id,
                "dest_gui": gui,
                "addr": addr,
                "root_addr": self.root_addr,
                "hop_count": self.hop_count + 1,
                "gui_index": assigned_gui_index,
                "visual_net_id": visual_net_id,
                "source_role": self.role.name,
                "assigned_role": assigned_role,
                "creation_time": self.now,
            }
        )
        if gui in self.neighbors_table:
            self.neighbors_table[gui]["addr"] = addr

    ###################
    def send_join_ack(self, dest):
        self.send(
            {
                "dest": dest,
                "type": "JOIN_ACK",
                "source": self.addr,
                "gui": self.id,
                "creation_time": self.now,
            }
        )

    ###################
    def send_neighbor_table_update(self):
        neighbor_data = {
            gui: {
                "hop_count": info["hop_count"],
                "addr": info.get("addr"),
                "ch_addr": info.get("ch_addr"),
                "distance": info.get("distance", 999),
            }
            for gui, info in self.neighbors_table.items()
        }
        self.send(
            {
                "dest": wsn.BROADCAST_ADDR,
                "type": "NEIGHBOR_TABLE_UPDATE",
                "gui": self.id,
                "source": self.addr,
                "neighbors": neighbor_data,
                "creation_time": self.now,
            }
        )

    ###################
    def find_node_by_addr(self, addr):
        """Find node GUI by its address in neighbor table"""
        for gui, info in self.neighbors_table.items():
            if info.get("addr") == addr:
                return gui
        return None

    def find_gui_by_addr(self, addr):
        for gui, info in self.neighbors_table.items():
            if info.get("addr") == addr:
                return gui
        return None

    def calculate_next_hop(self, pck):
        dest_addr = pck["dest"]

        # Initialize default routing method if not present
        if "routing_method" not in pck:
            pck["routing_method"] = "unknown"

        if "path" in pck and self.id in pck["path"][:-1]:
            return None

        # Mesh 1-hop: Check for valid address before comparison
        for gui, info in self.neighbors_table.items():
            neighbor_addr = info.get("addr")
            if neighbor_addr is not None and neighbor_addr == dest_addr:
                pck["routing_method"] = "mesh-1hop"
                return dest_addr

        dest_gui = None

        # Determine dest_gui: Check for valid address before comparison
        for gui, info in self.neighbors_table.items():
            neighbor_addr = info.get("addr")
            if neighbor_addr is not None and neighbor_addr == dest_addr:
                dest_gui = gui
                break

        if dest_gui is None:
            if hasattr(dest_addr, "node_addr"):
                dest_gui = dest_addr.node_addr

        if dest_gui is not None and dest_gui in self.routing_table:
            route_info = self.routing_table[dest_gui]
            next_hop_addr = route_info.get("next_hop")

            via_gui = route_info.get("via_gui")
            if via_gui in self.neighbors_table:
                pck["routing_method"] = "mesh-2hop"
                return next_hop_addr

        if self.role in [Roles.ROOT, Roles.CLUSTER_HEAD]:
            if pck.get("type") == "NETWORK_REPLY":
                dest_net = dest_addr.net_addr

                for child_gui, child_networks in self.child_networks_table.items():
                    if dest_net in child_networks:
                        if child_gui in self.neighbors_table:
                            pck["routing_method"] = "tree-downstream-reply"
                            return self.neighbors_table[child_gui]["addr"]

            if self.ch_addr is not None and dest_addr.net_addr == self.ch_addr.net_addr:
                # Downstream local: Check for valid address before comparison
                for gui, info in self.neighbors_table.items():
                    neighbor_addr = info.get("addr")
                    if neighbor_addr is not None and neighbor_addr == dest_addr:
                        pck["routing_method"] = "tree-downstream-local"
                        return dest_addr
            else:
                for child_gui, child_networks in self.child_networks_table.items():
                    if dest_addr.net_addr in child_networks:
                        if child_gui in self.neighbors_table:
                            pck["routing_method"] = "tree-downstream-remote"
                            return self.neighbors_table[child_gui]["addr"]

        if self.role != Roles.ROOT:
            if self.parent_gui is not None and self.parent_gui in self.neighbors_table:
                parent_info = self.neighbors_table[self.parent_gui]
                next_hop = parent_info.get("addr") or parent_info.get("ch_addr")
                if next_hop is not None:
                    pck["routing_method"] = "tree-upstream"
                    return next_hop

        return None

    ###################
    def route_and_forward_package(self, pck):
        if "path" not in pck:
            pck["path"] = []

        if len(pck["path"]) == 0 or pck["path"][-1] != self.id:
            pck["path"].append(self.id)

        if "ttl" not in pck:
            pck["ttl"] = 64

        pck["ttl"] -= 1
        if pck["ttl"] <= 0:
            return

        next_hop_val = pck.get("next_hop")
        if next_hop_val is not None:
            if next_hop_val == self.addr:
                pck["next_hop"] = None
            elif self.ch_addr is not None and next_hop_val == self.ch_addr:
                pck["next_hop"] = None

        next_hop = pck.get("next_hop")

        if next_hop is None and "source_route" in pck and pck["source_route"]:
            route = pck["source_route"]
            idx = pck.get("src_rt_idx", 0)

            curr_node_in_route = -1
            try:
                curr_node_in_route = route.index(self.id)
            except ValueError:
                if len(route) > 0:
                    target_gui = route[0]
                    if target_gui in self.neighbors_table:
                        next_hop = self.neighbors_table[target_gui]["addr"]
                        pck["src_rt_idx"] = 0
                    else:
                        pck["source_route"] = None

            if curr_node_in_route != -1 and curr_node_in_route + 1 < len(route):
                target_gui = route[curr_node_in_route + 1]
                if target_gui in self.neighbors_table:
                    next_hop = self.neighbors_table[target_gui]["addr"]
                    pck["src_rt_idx"] = curr_node_in_route + 1
                else:
                    pck["source_route"] = None

        if next_hop is None:
            next_hop = self.calculate_next_hop(pck)

        if next_hop is None:
            return

        if next_hop is not None:
            next_hop_gui = self.find_node_by_addr(next_hop)
            if (
                next_hop_gui is not None
                and "path" in pck
                and next_hop_gui in pck["path"]
            ):
                return

            pck["next_hop"] = next_hop

        if self.role == Roles.ROUTER:
            self.last_forward_time = self.now
            # Track Router Forwards
            self.router_forwards += 1

            # Forwarding Metrics
            if "creation_time" in pck:
                self.packet_delays.append(
                    {
                        "delay": self.now - pck["creation_time"],
                        "source": pck.get("source"),
                        "type": pck.get("type", "UNKNOWN") + "_FORWARD",
                        "time": self.now,
                        "hop_count": len(pck.get("path", [])),
                        "routing_method": pck.get("routing_method", "unknown"),
                    }
                )

        self.send(pck)

    ###################
    def send_network_request(self):
        """Sending network request message to root address to be cluster head"""
        if self.network_request_sent and not self.network_request_pending:
            return

        if self.root_addr is None:
            return

        if self.addr is None:
            return

        if self.parent_gui is None or self.parent_gui not in self.neighbors_table:
            return

        pck = {
            "dest": self.root_addr,
            "type": "NETWORK_REQUEST",
            "source": self.addr,
            "timestamp": self.now,
            "path": [],
            "routing_method": "tree-upstream",
        }

        self.network_request_sent = True

        self.route_and_forward_package(pck)

        self.network_request_pending = True
        self.network_request_attempts += 1

        self.set_timer("TIMER_NETWORK_REQUEST_RETRY", 10.0)

    ###################
    def send_network_reply(
        self, dest, addr, assigned_net_index=None, source_route=None
    ):
        routing_method = "tree-downstream"
        if source_route:
            routing_method = "source-routed"

        pck = {
            "dest": dest,
            "type": "NETWORK_REPLY",
            "source": self.addr,
            "addr": addr,
            "timestamp": self.now,
            "path": [self.id],
            "net_index": assigned_net_index,
            "source_route": source_route,
            "src_rt_idx": 0,
            "routing_method": routing_method,
        }
        self.route_and_forward_package(pck)

    ###################
    def send_network_update(self):
        child_networks = [self.ch_addr.net_addr]
        for networks in self.child_networks_table.values():
            child_networks.extend(networks)

        if self.parent_gui in self.neighbors_table:
            parent_info = self.neighbors_table[self.parent_gui]
            dest_addr = parent_info.get("ch_addr")

            if dest_addr is None:
                return

            self.send(
                {
                    "dest": dest_addr,
                    "type": "NETWORK_UPDATE",
                    "source": self.addr,
                    "gui": self.id,
                    "child_networks": child_networks,
                    "creation_time": self.now,
                    "routing_method": "tree-upstream",
                }
            )

    ###################
    def should_log_packet_metrics(self, pck):
        """Determine if packet type should be logged for metrics."""
        trackable_types = [
            "SENSOR",
            "NETWORK_REQUEST",
            "NETWORK_REPLY",
            "NETWORK_UPDATE",
        ]
        return pck.get("type") in trackable_types

    def is_final_destination(self, pck):
        """Check if this node is the final destination for the packet."""
        if pck.get("dest") == wsn.BROADCAST_ADDR:
            return False

        if self.addr is None:
            return False

        # Direct Match
        if pck.get("dest") == self.addr:
            return True

        # Cluster Head Match (if we are the CH)
        if self.role == Roles.CLUSTER_HEAD and pck.get("dest") == self.ch_addr:
            return True

        # Root Match (if we are Root)
        if self.role == Roles.ROOT and pck.get("dest") == self.root_addr:
            return True

        return False

    def log_packet_metrics(self, pck):
        """Log packet delay and routing metrics"""
        if "creation_time" in pck:
            delay = self.now - pck["creation_time"]
            routing_method = pck.get("routing_method", "unknown")

            # Infer routing method if not set
            if routing_method == "unknown":
                pck_type = pck.get("type")

                # Upstream packets (going to ROOT)
                if pck_type in ["SENSOR", "NETWORK_REQUEST", "NETWORK_UPDATE"]:
                    routing_method = "tree-upstream-inferred"

                # Downstream packets (coming from ROOT)
                elif pck_type == "NETWORK_REPLY":
                    if "source_route" in pck and pck["source_route"]:
                        routing_method = "source-routed-inferred"
                    else:
                        routing_method = "tree-downstream-inferred"

                # Determine by hop count as last resort
                elif "path" in pck:
                    path_len = len(pck["path"])
                    if path_len <= 1:
                        routing_method = "direct"
                    elif path_len == 2:
                        routing_method = "mesh-1hop-inferred"
                    else:
                        routing_method = "mesh-2hop-inferred"

            self.packet_delays.append(
                {
                    "delay": delay,
                    "source": pck.get("source"),
                    "type": pck.get("type"),
                    "time": self.now,
                    "hop_count": len(pck.get("path", [])),
                    "routing_method": routing_method,
                }
            )

        if "path" in pck:
            self.packet_paths.append(
                {
                    "path": pck["path"],
                    "source": pck.get("source"),
                    "dest": pck.get("dest"),
                    "type": pck.get("type"),
                    "time": self.now,
                }
            )

    ###################
    def on_receive(self, pck):
        """Executes when a package received."""
        if self.is_dead:
            return

        # Centralized Logging Check
        if self.should_log_packet_metrics(pck):
            if self.is_final_destination(pck):
                self.log_packet_metrics(pck)

        is_broadcast = pck.get("dest") == wsn.BROADCAST_ADDR

        if not is_broadcast:
            if self.addr is None:
                return

            if "next_hop" in pck:
                if pck["next_hop"] is not None:
                    if pck["next_hop"] != self.addr and pck["next_hop"] != self.ch_addr:
                        return
            else:
                if pck["dest"] != self.addr and pck["dest"] != self.ch_addr:
                    return

        if pck["type"] == "JOIN_REPLY":
            dest_gui = pck.get("dest_gui")
            if dest_gui in self.pending_join_replies:
                timer_name = self.pending_join_replies[dest_gui]["timer_name"]
                self.kill_timer(timer_name)
                del self.pending_join_replies[dest_gui]

        if (
            self.role == Roles.ROOT
            or self.role == Roles.CLUSTER_HEAD
            or self.role == Roles.ROUTER
        ):
            should_forward = False
            if "next_hop" in pck.keys() and pck["dest"] != self.addr:
                if self.role == Roles.ROUTER:
                    should_forward = True
                elif pck["dest"] != self.ch_addr:
                    should_forward = True

            if should_forward:
                self.route_and_forward_package(pck)
                return

            if pck["type"] == "HEART_BEAT":
                self.update_neighbor(pck)

                if (
                    self.role == Roles.CLUSTER_HEAD
                    and pck["role"] == Roles.CLUSTER_HEAD
                ):
                    dist = self.neighbors_table[pck["gui"]].get("distance", 999)
                    overlap_threshold = 60
                    if dist < overlap_threshold:
                        if self.id > pck["gui"]:
                            if self.is_at_cluster_boundary():
                                self.become_router_from_ch()
                            else:
                                self.last_demotion_time = self.now
                                self.become_unregistered()

            if pck["type"] == "NEIGHBOR_TABLE_UPDATE":
                sender_gui = pck["gui"]
                if sender_gui in self.neighbors_table:
                    self.neighbors_table[sender_gui]["neighbors"] = pck["neighbors"]

                self.process_routing_update(sender_gui, pck["source"], pck["neighbors"])

            if pck["type"] == "PROBE":
                self.send_heart_beat()

            if pck["type"] == "JOIN_REQUEST" and self.role in [
                Roles.ROOT,
                Roles.CLUSTER_HEAD,
                Roles.ROUTER,
            ]:
                requestor_gui = pck["gui"]
                preferred = pck.get("preferred_parent")
                forwarded_by = pck.get("forwarded_by")

                if self.role == Roles.ROUTER:
                    if self.parent_gui in self.neighbors_table:
                        parent_info = self.neighbors_table[self.parent_gui]
                        parent_addr = parent_info.get("ch_addr")
                        if parent_addr is None:
                            parent_addr = parent_info.get("addr")

                        if parent_addr:
                            forward_pck = pck.copy()
                            forward_pck["path"] = list(pck.get("path", []))
                            forward_pck["dest"] = parent_addr
                            forward_pck["forwarded_by"] = self.id
                            forward_pck["original_requestor"] = requestor_gui
                            self.route_and_forward_package(forward_pck)
                    return

                if (
                    self.role == Roles.CLUSTER_HEAD
                    and self.current_children >= self.max_children
                ):
                    if forwarded_by and forwarded_by in self.neighbors_table:
                        self.send_delegate_to_ch(forwarded_by)
                    return

                if preferred is None or preferred == self.id:
                    idx = self.next_gui_index
                    self.next_gui_index += 1

                    visual_net = (
                        self.gui_addr.major if self.gui_addr else self.addr.net_addr
                    )

                    if self.role == Roles.ROOT:
                        assigned_addr = wsn.Addr(self.addr.net_addr, requestor_gui)
                    else:
                        assigned_addr = wsn.Addr(self.ch_addr.net_addr, requestor_gui)

                    self.send_join_reply(
                        requestor_gui,
                        assigned_addr,
                        assigned_gui_index=idx,
                        visual_net_id=visual_net,
                    )
                    self.send_neighbor_table_update()
                else:
                    timer_name = f"TIMER_FALLBACK_REPLY_{requestor_gui}"
                    self.pending_join_replies[requestor_gui] = {
                        "timer_name": timer_name,
                        "requestor": requestor_gui,
                        "preferred": preferred,
                        "timestamp": self.now,
                    }
                    self.set_timer(timer_name, self.fallback_response_delay)

            if pck["type"] == "NETWORK_REQUEST":
                if self.role == Roles.ROOT:
                    source_key = str(pck["source"])
                    current_time = self.now

                    if source_key in self.processed_network_requests:
                        last_processed = self.processed_network_requests[source_key]
                        if (current_time - last_processed) < self.request_dedup_window:
                            self.duplicate_requests_blocked += 1
                            return

                    keys_to_remove = [
                        k
                        for k, v in self.processed_network_requests.items()
                        if (current_time - v) > self.request_dedup_window
                    ]
                    for k in keys_to_remove:
                        del self.processed_network_requests[k]

                    self.processed_network_requests[source_key] = current_time

                    # Log removed here (handled by centralized check)

                    request_path = pck.get("path", [])

                    source_route = list(reversed(request_path))

                    new_addr = wsn.Addr(pck["source"].node_addr, 254)

                    net_idx = self.next_network_index
                    self.next_network_index += 1

                    self.send_network_reply(
                        pck["source"],
                        new_addr,
                        assigned_net_index=net_idx,
                        source_route=source_route,
                    )
            if pck["type"] == "JOIN_ACK":
                self.members_table.append(pck["gui"])
                self.current_children += 1

                # Log Member Join
                self.cluster_events.append(
                    {
                        "time": self.now,
                        "event_type": "MEMBER_JOINED",
                        "node_id": self.id,
                        "member_id": pck["gui"],
                        "total_members": len(self.members_table),
                    }
                )

                if self.current_children >= self.max_children:
                    self.capacity_reached = True
                    self.broadcast_capacity_status()

                self.send_neighbor_table_update()

            if pck["type"] == "NETWORK_UPDATE":
                self.child_networks_table[pck["gui"]] = pck["child_networks"]

                # Log Child Network Added
                self.cluster_events.append(
                    {
                        "time": self.now,
                        "event_type": "CHILD_NETWORK_ADDED",
                        "node_id": self.id,
                        "child_gui": pck["gui"],
                        "networks": pck["child_networks"],
                    }
                )

                if self.role != Roles.ROOT:
                    self.send_network_update()
            if pck["type"] == "SENSOR":
                if self.role == Roles.ROOT:
                    pass

            if pck["type"] == "CAPACITY_STATUS":
                if pck["gui"] == self.parent_gui:
                    self.parent_at_capacity = pck["at_capacity"]

        elif self.role == Roles.REGISTERED:
            if pck["type"] == "HEART_BEAT":
                self.update_neighbor(pck)
            if pck["type"] == "PROBE":
                self.send_heart_beat()

            if pck["type"] == "JOIN_REQUEST":
                requestor_gui = pck["gui"]

                if not hasattr(self, "pending_child_joins"):
                    self.pending_child_joins = []
                self.pending_child_joins.append(pck)

                if self.should_promote_to_ch():
                    if (
                        not self.network_request_sent
                        and not self.network_request_pending
                    ):
                        delay = 0.5
                        self.set_timer("TIMER_NETWORK_REQUEST", delay)
                else:
                    if self.parent_gui in self.neighbors_table:
                        parent_info = self.neighbors_table[self.parent_gui]
                        parent_addr = parent_info.get("ch_addr") or parent_info.get(
                            "addr"
                        )
                        if parent_addr:
                            forward_pck = pck.copy()
                            forward_pck["dest"] = parent_addr
                            forward_pck["forwarded_by"] = self.id
                            self.route_and_forward_package(forward_pck)

            if pck["type"] == "NETWORK_REPLY":
                net_idx = pck.get("net_index")
                self.become_cluster_head(
                    pck["addr"].net_addr, assigned_net_index=net_idx
                )

            if pck["type"] == "DELEGATE_TO_CH":
                if pck["gui"] == self.parent_gui:
                    if (
                        not self.network_request_sent
                        and not self.network_request_pending
                    ):
                        self.set_timer("TIMER_NETWORK_REQUEST", 0.1)

            if pck["type"] == "CAPACITY_STATUS":
                if pck["gui"] == self.parent_gui:
                    self.parent_at_capacity = pck["at_capacity"]

        elif self.role == Roles.UNDISCOVERED:
            if pck["type"] == "HEART_BEAT":
                self.update_neighbor(pck)
                self.kill_timer("TIMER_PROBE")
                self.become_unregistered()

        if self.role == Roles.UNREGISTERED:
            if pck["type"] == "HEART_BEAT":
                self.update_neighbor(pck)

            if pck["type"] == "JOIN_REPLY":
                if pck["dest_gui"] == self.id:
                    self.parent_gui = pck["gui"]
                    self.root_addr = pck["root_addr"]
                    self.hop_count = pck["hop_count"]
                    self.root_id = (
                        pck["root_addr"].net_addr if pck["root_addr"] else None
                    )

                    self.draw_parent()
                    self.kill_timer("TIMER_JOIN_REQUEST")

                    pck["ch_addr"] = pck["source"]
                    pck["addr"] = pck["source"]
                    self.update_neighbor(pck)

                    self.send_join_ack(pck["source"])
                    if self.ch_addr is not None:
                        self.set_role(Roles.CLUSTER_HEAD)
                        self.send_network_update()
                    else:
                        idx = pck.get("gui_index")
                        v_net = pck.get("visual_net_id")
                        assigned_role = pck.get("assigned_role", "REGISTERED")

                        if assigned_role == "ROUTER":
                            self.become_router(
                                pck["source"],
                                assigned_gui_index=idx,
                                visual_net_id=v_net,
                            )
                        else:
                            self.become_registered(
                                pck["source"],
                                assigned_gui_index=idx,
                                visual_net_id=v_net,
                            )

                    self.send_heart_beat()

    ###################
    def on_timer_fired(self, name, *args, **kwargs):
        """Executes when a timer fired."""
        if self.is_dead:
            return

        # Delayed Send Execution
        if name.startswith("TIMER_DELAYED_SEND_"):
            pck = kwargs.get("pck")
            if pck:
                # Call parent class send() to actually put it on wire
                super().send(pck)

        elif name == "TIMER_ARRIVAL":
            self.scene.nodecolor(self.id, 1, 0, 0)
            self.wake_up()
            self.set_timer("TIMER_PROBE", 1)

        elif name == "TIMER_PROBE":
            if self.c_probe < self.th_probe:
                self.send_probe()
                self.c_probe += 1
                self.set_timer("TIMER_PROBE", 1)
            else:
                if self.is_root_eligible:
                    self.become_root()
                else:
                    self.c_probe = 0
                    self.set_timer("TIMER_PROBE", 30)

        # --- FAILURE INJECTION HANDLING ---
        elif name.startswith("TIMER_KILL_NODE_"):
            self.die()

        elif name == "TIMER_HEART_BEAT":
            self.send_heart_beat()

            # --- Check parent liveness ---
            if self.role in [Roles.REGISTERED, Roles.ROUTER, Roles.CLUSTER_HEAD]:
                if not self.check_parent_alive():
                    self.detect_parent_failure()

            if self.role in [Roles.REGISTERED, Roles.ROUTER]:
                if not self.validate_address_consistency():
                    if self.parent_gui in self.neighbors_table:
                        parent_info = self.neighbors_table[self.parent_gui]
                        parent_ch = parent_info.get("ch_addr")

                        if parent_ch:
                            self.ch_addr = parent_ch
                            if self.addr:
                                self.addr = wsn.Addr(parent_ch.net_addr, self.id)
                            if self.gui_addr:
                                old_minor = self.gui_addr.minor

                                ch_visual_net = parent_ch.net_addr
                                parent_gui_addr = parent_info.get("gui_addr")
                                if parent_gui_addr:
                                    ch_visual_net = parent_gui_addr.major

                                self.gui_addr = GUIAddr(ch_visual_net, old_minor)

                                if self.role == Roles.ROUTER:
                                    self.set_label(f"{self.gui_addr}")
                                else:
                                    self.set_label(str(self.gui_addr))

            if self.role == Roles.REGISTERED:
                pass

            elif self.role == Roles.ROUTER:
                if self.should_demote_router():
                    self.set_role(Roles.REGISTERED)
                    self.pending_child_joins = []
                    if self.gui_addr:
                        self.set_label(str(self.gui_addr))
                    self.send_heart_beat()

            if self.role in [Roles.REGISTERED, Roles.CLUSTER_HEAD, Roles.ROOT]:
                self.send_neighbor_table_update()

            self.cleanup_routing_table()

            self.set_timer("TIMER_HEART_BEAT", config.HEARTH_BEAT_TIME_INTERVAL)

        elif name == "TIMER_JOIN_REQUEST":
            if self.unregistered_since is not None:
                time_unregistered = self.now - self.unregistered_since
                if time_unregistered > self.forced_reprobe_threshold:
                    current_time = self.now
                    stale_threshold = 30

                    valid_neighbors = {}
                    for gui, info in self.neighbors_table.items():
                        last_heard = info.get("last_heard", 0)
                        if (current_time - last_heard) < stale_threshold:
                            valid_neighbors[gui] = info

                    if len(valid_neighbors) == 0:
                        self.neighbors_table = {}
                        self.candidate_parents_table = []
                    else:
                        self.neighbors_table = valid_neighbors
                        self.candidate_parents_table = list(valid_neighbors.keys())

                    self.last_join_parent = None
                    self.join_retry_count = 0
                    self.unregistered_since = self.now

                    self.c_probe = 0
                    self.send_probe()
                    self.set_timer("TIMER_JOIN_REQUEST", 15)
                    return

            if len(self.candidate_parents_table) == 0:
                self.become_unregistered()
            else:
                self.last_status_log = self.now
                self.select_and_join()

        elif name == "TIMER_UNREGISTERED_PROBE":
            if self.role == Roles.UNREGISTERED:
                self.send_probe()
                self.last_unregistered_probe = self.now
                self.set_timer(
                    "TIMER_UNREGISTERED_PROBE", self.unregistered_probe_interval
                )

        elif name == "TIMER_BROADCAST_JOIN":
            if self.role == Roles.UNREGISTERED:
                for gui in self.candidate_parents_table:
                    if gui in self.neighbors_table:
                        addr = self.neighbors_table[gui].get("source")
                        if addr:
                            self.send_join_request(addr, preferred_parent_gui=gui)

        elif name == "TIMER_NETWORK_REQUEST":
            if (
                self.role in [Roles.REGISTERED, Roles.ROUTER]
                and self.root_addr is not None
            ):
                if self.parent_gui in self.neighbors_table:
                    self.send_network_request()

        elif name == "TIMER_NETWORK_REQUEST_RETRY":
            if self.network_request_pending:
                if self.network_request_attempts >= self.max_network_request_attempts:
                    self.network_request_pending = False
                    self.network_request_attempts = 0
                else:
                    if self.role in [Roles.REGISTERED, Roles.ROUTER]:
                        if self.parent_gui in self.neighbors_table:
                            self.network_request_sent = False
                            self.send_network_request()
                        else:
                            self.network_request_pending = False
                    else:
                        self.network_request_pending = False

        elif name.startswith("TIMER_FALLBACK_REPLY_"):
            requestor_gui = int(name.split("_")[-1])

            if requestor_gui in self.pending_join_replies:
                if self.current_children >= self.max_children:
                    del self.pending_join_replies[requestor_gui]
                    return

                idx = self.next_gui_index
                self.next_gui_index += 1
                visual_net = (
                    self.gui_addr.major if self.gui_addr else self.addr.net_addr
                )

                self.send_join_reply(
                    requestor_gui,
                    wsn.Addr(self.ch_addr.net_addr, requestor_gui),
                    assigned_gui_index=idx,
                    visual_net_id=visual_net,
                )
                self.send_neighbor_table_update()
                del self.pending_join_replies[requestor_gui]

        elif name == "TIMER_SENSOR":
            if self.root_addr is not None:
                pck = {
                    "dest": self.root_addr,
                    "type": "SENSOR",
                    "source": self.addr,
                    "sensor_value": random.uniform(10, 50),
                    "timestamp": self.now,
                    "creation_time": self.now,
                    "path": [self.id],
                    "routing_method": "tree-upstream",
                }
                self.route_and_forward_package(pck)
            timer_duration = self.id % 20
            if timer_duration == 0:
                timer_duration = 1
            self.set_timer("TIMER_SENSOR", timer_duration)

        elif name == "TIMER_EXPORT_CH_CSV":
            if self.role == Roles.ROOT:
                try:
                    write_clusterhead_distances_csv("clusterhead_distances.csv")
                except Exception:
                    pass
                self.set_timer("TIMER_EXPORT_CH_CSV", config.EXPORT_CH_CSV_INTERVAL)

        elif name == "TIMER_EXPORT_NEIGHBOR_CSV":
            if self.role == Roles.ROOT:
                try:
                    write_neighbor_distances_csv("neighbor_distances.csv")
                except Exception:
                    pass
                self.set_timer(
                    "TIMER_EXPORT_NEIGHBOR_CSV", config.EXPORT_NEIGHBOR_CSV_INTERVAL
                )


ROOT_ID = 0  # ROOT IS ALWAYS NODE 0
ROOT_POS_INDEX = random.randrange(config.SIM_NODE_COUNT)  # RANDOM POSITION FOR ROOT


def write_node_distances_csv(path="node_distances.csv"):
    """Write pairwise node-to-node Euclidean distances as an edge list."""
    if SIMULATION_CONTEXT is None: return
    ids = sorted(SIMULATION_CONTEXT.node_positions.keys())
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["source_id", "target_id", "distance"])
        for i, sid in enumerate(ids):
            x1, y1 = SIMULATION_CONTEXT.node_positions[sid]
            for tid in ids[i + 1 :]:  # i+1 to avoid duplicates and self-pairs
                x2, y2 = SIMULATION_CONTEXT.node_positions[tid]
                dist = math.hypot(x1 - x2, y1 - y2)
                w.writerow([sid, tid, f"{dist:.6f}"])


def write_node_distance_matrix_csv(path="node_distance_matrix.csv"):
    if SIMULATION_CONTEXT is None: return
    ids = sorted(SIMULATION_CONTEXT.node_positions.keys())
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["node_id"] + ids)
        for sid in ids:
            x1, y1 = SIMULATION_CONTEXT.node_positions[sid]
            row = [sid]
            for tid in ids:
                x2, y2 = SIMULATION_CONTEXT.node_positions[tid]
                dist = math.hypot(x1 - x2, y1 - y2)
                row.append(f"{dist:.6f}")
            w.writerow(row)


def write_clusterhead_distances_csv(path="clusterhead_distances.csv"):
    """Write pairwise distances between current cluster heads."""
    if SIMULATION_CONTEXT is None: return
    clusterheads = []
    for node in sim.nodes:
        if (
            hasattr(node, "role")
            and node.role == Roles.CLUSTER_HEAD
            and node.id in SIMULATION_CONTEXT.node_positions
        ):
            x, y = SIMULATION_CONTEXT.node_positions[node.id]
            clusterheads.append((node.id, x, y))

    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["clusterhead_1", "clusterhead_2", "distance"])
        if len(clusterheads) >= 2:
            for i, (id1, x1, y1) in enumerate(clusterheads):
                for id2, x2, y2 in clusterheads[i + 1 :]:
                    dist = math.hypot(x1 - x2, y1 - y2)
                    w.writerow([id1, id2, f"{dist:.6f}"])


def write_neighbor_distances_csv(path="neighbor_distances.csv", dedupe_undirected=True):
    """Export neighbor distances per node."""
    if SIMULATION_CONTEXT is None or not SIMULATION_CONTEXT.node_positions:
        return

    seen_pairs = set()

    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "node_id",
                "neighbor_id",
                "distance",
                "neighbor_role",
                "neighbor_hop_count",
                "arrival_time",
            ]
        )

        for node in sim.nodes:
            if not hasattr(node, "neighbors_table"):
                continue

            x1, y1 = SIMULATION_CONTEXT.node_positions.get(node.id, (None, None))
            if x1 is None:
                continue

            for n_gui, pck in getattr(node, "neighbors_table", {}).items():
                try:
                    if dedupe_undirected:
                        key = (min(node.id, n_gui), max(node.id, n_gui))
                        if key in seen_pairs:
                            continue
                        seen_pairs.add(key)

                    x2, y2 = SIMULATION_CONTEXT.node_positions.get(n_gui, (None, None))
                    if x2 is None:
                        continue

                    dist = pck.get("distance")
                    if dist is None:
                        dist = math.hypot(x1 - x2, y1 - y2)

                    raw_role = pck.get("role")
                    if raw_role is not None:
                        n_role = getattr(raw_role, "name", raw_role)
                    else:
                        n_role = pck.get("source_role", "MISSING_ROLE_DATA")

                    hop = pck.get("hop_count", "")
                    at = pck.get("arrival_time", "")

                    w.writerow([node.id, n_gui, f"{dist:.6f}", n_role, hop, at])

                except Exception:
                    continue


def write_packet_metrics_csv(path="packet_metrics.csv"):
    """Write packet delay and path metrics"""
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "node_id",
                "packet_type",
                "source",
                "delay",
                "time",
                "hop_count",
                "routing_method",
            ]
        )

        for node in sim.nodes:
            if hasattr(node, "packet_delays"):
                for metric in node.packet_delays:
                    w.writerow(
                        [
                            node.id,
                            metric.get("type", ""),
                            metric.get("source", ""),
                            f"{metric.get('delay', 0):.6f}",
                            f"{metric.get('time', 0):.2f}",
                            metric.get("hop_count", 0),
                            metric.get("routing_method", "unknown"),
                        ]
                    )


def write_packet_paths_csv(path="packet_paths.csv"):
    """Write packet path traces"""
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            ["node_id", "packet_type", "source", "dest", "path", "hop_count", "time"]
        )

        for node in sim.nodes:
            if hasattr(node, "packet_paths"):
                for metric in node.packet_paths:
                    path_str = " -> ".join(map(str, metric.get("path", [])))
                    w.writerow(
                        [
                            node.id,
                            metric.get("type", ""),
                            metric.get("source", ""),
                            metric.get("dest", ""),
                            path_str,
                            len(metric.get("path", [])) - 1,
                            f"{metric.get('time', 0):.2f}",
                        ]
                    )


def write_join_times_csv(path="join_times.csv"):
    """Export join time metrics for all nodes."""
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["node_id", "start_time", "join_time", "duration"])
        for node in sim.nodes:
            if hasattr(node, "join_duration") and node.join_duration is not None:
                join_time = node.start_time + node.join_duration
                w.writerow(
                    [
                        node.id,
                        f"{node.start_time:.2f}",
                        f"{join_time:.2f}",
                        f"{node.join_duration:.2f}",
                    ]
                )


# --- Role Events CSV Writers ---


def write_role_events_csv(path="role_events.csv"):
    """Export detailed role events log."""
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time", "node_id", "event_type", "details"])

        for node in sim.nodes:
            if hasattr(node, "role_events"):
                for event in node.role_events:
                    details_str = str(event["details"])
                    w.writerow(
                        [
                            f"{event['time']:.2f}",
                            event["node_id"],
                            event["event_type"],
                            details_str,
                        ]
                    )


def write_recovery_metrics_csv(path="recovery_metrics.csv"):
    """Export recovery duration metrics."""
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["node_id", "orphan_time", "recovery_time", "recovery_duration"])

        for node in sim.nodes:
            if hasattr(node, "role_events"):
                for event in node.role_events:
                    if event["event_type"] == "RECOVERY_COMPLETE":
                        w.writerow(
                            [
                                node.id,
                                f"{event['details']['orphan_time']:.2f}",
                                f"{event['time']:.2f}",
                                f"{event['details']['recovery_duration']:.2f}",
                            ]
                        )


def write_orphan_metrics_csv(path="orphan_metrics.csv"):
    """Export global orphan count history."""
    if SIMULATION_CONTEXT is None: return
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time", "orphan_count"])
        for time, count in SIMULATION_CONTEXT.orphan_history:
            w.writerow([f"{time:.2f}", count])


# --- Join Network Events CSV Writers ---
def write_cluster_events_csv(path="cluster_events.csv"):
    """Export cluster formation and membership changes."""
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time", "node_id", "event_type", "details"])

        for node in sim.nodes:
            if hasattr(node, "cluster_events"):
                for event in node.cluster_events:
                    details = ""
                    if event["event_type"] == "CLUSTER_FORMED":
                        details = (
                            f"net_id={event['net_id']},visual={event['visual_net']}"
                        )
                    elif event["event_type"] == "MEMBER_JOINED":
                        details = f"member={event['member_id']},total={
                            event['total_members']
                        }"
                    elif event["event_type"] == "CHILD_NETWORK_ADDED":
                        details = (
                            f"child={event['child_gui']},networks={event['networks']}"
                        )
                    elif event["event_type"] == "BECAME_ROUTER":
                        details = f"parent={event['parent_gui']},previous={
                            event['previous_role']
                        }"

                    w.writerow(
                        [
                            f"{event['time']:.2f}",
                            event["node_id"],
                            event["event_type"],
                            details,
                        ]
                    )


def write_packet_loss_csv(path="packet_loss.csv"):
    """Export packet loss statistics."""
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["node_id", "packets_sent", "packets_dropped", "loss_rate"])

        for node in sim.nodes:
            if hasattr(node, "packets_sent") and node.packets_sent > 0:
                loss_rate = node.packets_dropped / node.packets_sent
                w.writerow(
                    [
                        node.id,
                        node.packets_sent,
                        node.packets_dropped,
                        f"{loss_rate:.4f}",
                    ]
                )


# --- Failure CSV Writers ---
def write_failure_events_csv(path="failure_events.csv"):
    """Export node failure and recovery events."""
    if SIMULATION_CONTEXT is None: return
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["failure_id", "time", "node_id", "event_type", "role", "duration"])

        for event in SIMULATION_CONTEXT.failure_events:
            duration = event.get("recovery_duration", "")
            if duration:
                duration = f"{duration:.2f}"

            w.writerow(
                [
                    event["failure_id"],
                    f"{event['time']:.2f}",
                    event["node_id"],
                    event["event_type"],
                    event.get("role_at_death") or event.get("new_role", ""),
                    duration,
                ]
            )


###########################################################
def create_network(node_class, number_of_nodes=100):
    """Creates given number of nodes at random positions with random arrival times."""
    global SIMULATION_CONTEXT
    SIMULATION_CONTEXT = SimulationContext() # Reset context on new network
    
    edge = math.ceil(math.sqrt(number_of_nodes))
    for i in range(number_of_nodes):
        pos_index = i
        if i == 0:
            pos_index = ROOT_POS_INDEX
            print(f"PLACING ROOT (Node 0) at Grid Index {pos_index}")
        elif i == ROOT_POS_INDEX:
            pos_index = 0

        x = pos_index // edge
        y = pos_index % edge
        px = (
            300
            + config.SCALE * x * config.SIM_NODE_PLACING_CELL_SIZE
            + random.uniform(
                -1 * config.SIM_NODE_PLACING_CELL_SIZE / 3,
                config.SIM_NODE_PLACING_CELL_SIZE / 3,
            )
        )
        py = (
            200
            + config.SCALE * y * config.SIM_NODE_PLACING_CELL_SIZE
            + random.uniform(
                -1 * config.SIM_NODE_PLACING_CELL_SIZE / 3,
                config.SIM_NODE_PLACING_CELL_SIZE / 3,
            )
        )
        node = sim.add_node(node_class, (px, py))
        SIMULATION_CONTEXT.node_positions[node.id] = (px, py)
        node.tx_range = config.NODE_TX_RANGE * config.SCALE
        node.logging = True
        node.arrival = random.uniform(0, config.NODE_ARRIVAL_MAX)
        if node.id == ROOT_ID:
            node.arrival = 0.1

if __name__ == "__main__":
    sim = wsn.Simulator(
        duration=config.SIM_DURATION,
        timescale=config.SIM_TIME_SCALE,
        visual=config.SIM_VISUALIZATION,
        terrain_size=config.SIM_TERRAIN_SIZE,
        title=config.SIM_TITLE,
    )

    # creating random network
    create_network(SensorNode, config.SIM_NODE_COUNT)

    # --- FAILURE INJECTION SCHEDULING ---
    if ENABLE_FAILURES:
        for failure_time, node_id in FAILURE_SCHEDULE:
            # Find the node object by ID
            target_node = next((n for n in sim.nodes if n.id == node_id), None)
            if target_node:
                print(f"SCHEDULING FAILURE: Node {node_id} at t={failure_time}")
                target_node.set_timer(f"TIMER_KILL_NODE_{node_id}", failure_time)

    write_node_distances_csv("node_distances.csv")
    write_node_distance_matrix_csv("node_distance_matrix.csv")

    # start the simulation
    sim.run()
    print("Simulation Finished")

    # Export all metrics
    write_packet_metrics_csv("packet_metrics.csv")
    write_packet_paths_csv("packet_paths.csv")
    write_join_times_csv("join_times.csv")
    write_role_events_csv("role_events.csv")
    write_recovery_metrics_csv("recovery_metrics.csv")
    write_orphan_metrics_csv("orphan_metrics.csv")


    # Join Network Events
    write_cluster_events_csv("cluster_events.csv")

    # packet loss metrics
    write_packet_loss_csv("packet_loss.csv")

    # failure logging
    write_failure_events_csv("failure_events.csv")
