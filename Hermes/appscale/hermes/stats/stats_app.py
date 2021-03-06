""" Module responsible for configuring Stats API and stats profiling. """
import json
import time
from datetime import datetime

import attr
import logging
from tornado.ioloop import PeriodicCallback, IOLoop

from appscale.hermes.handlers import Respond404Handler
from appscale.hermes.stats.constants import (
  NODES_STATS_CONFIGS_NODE,
  PROCESSES_STATS_CONFIGS_NODE,
  PROXIES_STATS_CONFIGS_NODE
)
from appscale.hermes.stats.profile import (
  NodesProfileLog, ProcessesProfileLog, ProxiesProfileLog
)
from appscale.hermes.stats.converter import IncludeLists
from appscale.hermes.stats.handlers import (
  CurrentStatsHandler, CurrentClusterStatsHandler
)
from appscale.hermes.stats.producers.cluster_stats import (
  ClusterNodesStatsSource, ClusterProcessesStatsSource,
  ClusterPushQueueStatsSource, ClusterProxiesStatsSource,
  ClusterRabbitMQStatsSource
)
from appscale.hermes.stats.producers.node_stats import NodeStatsSource
from appscale.hermes.stats.producers.process_stats import ProcessesStatsSource
from appscale.hermes.stats.producers.proxy_stats import ProxiesStatsSource
from appscale.hermes.stats.producers.rabbitmq_stats import PushQueueStatsSource
from appscale.hermes.stats.producers.rabbitmq_stats import RabbitMQStatsSource


DEFAULT_INCLUDE_LISTS = IncludeLists({
  # Node stats
  'node': ['utc_timestamp', 'cpu', 'memory',
           'partitions_dict', 'loadavg'],
  'node.cpu': ['percent', 'count'],
  'node.memory': ['available', 'total'],
  'node.partition': ['free', 'used'],
  'node.loadavg': ['last_5min'],
  # Processes stats
  'process': ['monit_name', 'unified_service_name', 'application_id',
              'port', 'cpu', 'memory', 'children_stats_sum'],
  'process.cpu': ['user', 'system', 'percent'],
  'process.memory': ['resident', 'virtual', 'unique'],
  'process.children_stats_sum': ['cpu', 'memory'],
  # Proxies stats
  'proxy': ['name', 'unified_service_name', 'application_id',
            'frontend', 'backend', 'servers_count'],
  'proxy.frontend': ['bin', 'bout', 'scur', 'smax', 'rate',
                     'req_rate', 'req_tot', 'hrsp_4xx', 'hrsp_5xx'],
  'proxy.backend': ['qcur', 'scur', 'hrsp_5xx', 'qtime', 'rtime'],
  'rabbitmq': ['utc_timestamp', 'disk_free_alarm', 'mem_alarm', 'name'],
  'queue': ['name', 'messages'],
})


@attr.s
class HandlerInfo(object):
  """ Container for handler information. """
  handler_class = attr.ib()
  init_kwargs = attr.ib()


def get_local_stats_api_routes(is_lb_node, is_tq_node):
  """ Creates stats sources and API handlers for providing local
  node, processes and proxies (only on LB nodes) stats.

  Args:
    is_lb_node: A boolean indicating whether this node is load balancer.
    is_tq_node: A boolean indicating whether this node runs taskqueue services.
  Returns:
    A list of route-handler tuples.
  """

  # Any node provides its node and processes stats
  local_node_stats_handler =  HandlerInfo(
    handler_class=CurrentStatsHandler,
    init_kwargs={'source': NodeStatsSource,
                 'default_include_lists': DEFAULT_INCLUDE_LISTS})
  local_processes_stats_handler = HandlerInfo(
    handler_class=CurrentStatsHandler,
    init_kwargs={'source': ProcessesStatsSource,
                 'default_include_lists': DEFAULT_INCLUDE_LISTS})

  if is_lb_node:
    # Only LB nodes provide proxies stats
    local_proxies_stats_handler = HandlerInfo(
      handler_class=CurrentStatsHandler,
      init_kwargs={'source': ProxiesStatsSource,
                   'default_include_lists': DEFAULT_INCLUDE_LISTS}
    )
  else:
    # Stub handler for non-LB nodes
    local_proxies_stats_handler = HandlerInfo(
      handler_class=Respond404Handler,
      init_kwargs={'reason': 'Only LB node provides proxies stats'}
    )

  if is_tq_node:
    # Only TQ nodes provide RabbitMQ stats.
    local_rabbitmq_stats_handler = HandlerInfo(
      handler_class=CurrentStatsHandler,
      init_kwargs={'source': RabbitMQStatsSource,
                   'default_include_lists': DEFAULT_INCLUDE_LISTS}
    )
    local_push_queue_stats_handler = HandlerInfo(
      handler_class=CurrentStatsHandler,
      init_kwargs={'source': PushQueueStatsSource,
                   'default_include_lists': DEFAULT_INCLUDE_LISTS}
    )
  else:
    # Stub handler for non-TQ nodes
    local_rabbitmq_stats_handler = HandlerInfo(
      handler_class=Respond404Handler,
      init_kwargs={'reason': 'Only TQ nodes provide RabbitMQ stats'}
    )
    local_push_queue_stats_handler = HandlerInfo(
      handler_class=Respond404Handler,
      init_kwargs={'reason': 'Only TQ nodes provide push queue stats'}
    )

  routes = {
    '/stats/local/node': local_node_stats_handler,
    '/stats/local/processes': local_processes_stats_handler,
    '/stats/local/proxies': local_proxies_stats_handler,
    '/stats/local/rabbitmq': local_rabbitmq_stats_handler,
    '/stats/local/push_queues': local_push_queue_stats_handler,
  }
  return [
    (route, handler.handler_class, handler.init_kwargs)
    for route, handler in routes.iteritems()
  ]


def get_cluster_stats_api_routes(is_master):
  """ Creates stats sources and API handlers for providing cluster
  node, processes and proxies stats (on master node only).
  If this node is slave, it creates stub handlers for cluster stats routes.

  Args:
    is_master: A boolean indicating whether this node is master.
  Returns:
    A list of route-handler tuples.
  """
  if is_master:
    # Only master node provides cluster stats
    cluster_node_stats_handler = HandlerInfo(
      handler_class=CurrentClusterStatsHandler,
      init_kwargs={'source': ClusterNodesStatsSource(),
                   'default_include_lists': DEFAULT_INCLUDE_LISTS}
    )
    cluster_processes_stats_handler = HandlerInfo(
      handler_class=CurrentClusterStatsHandler,
      init_kwargs={'source': ClusterProcessesStatsSource(),
                   'default_include_lists': DEFAULT_INCLUDE_LISTS}
    )
    cluster_proxies_stats_handler = HandlerInfo(
      handler_class=CurrentClusterStatsHandler,
      init_kwargs={'source': ClusterProxiesStatsSource(),
                   'default_include_lists': DEFAULT_INCLUDE_LISTS}
    )
    cluster_rabbitmq_stats_handler = HandlerInfo(
      handler_class=CurrentClusterStatsHandler,
      init_kwargs={'source': ClusterRabbitMQStatsSource(),
                   'default_include_lists': DEFAULT_INCLUDE_LISTS}
    )
    cluster_push_queue_stats_handler = HandlerInfo(
      handler_class=CurrentClusterStatsHandler,
      init_kwargs={'source': ClusterPushQueueStatsSource(),
                   'default_include_lists': DEFAULT_INCLUDE_LISTS}
    )
  else:
    # Stub handler for slave nodes
    cluster_stub_handler = HandlerInfo(
      handler_class=Respond404Handler,
      init_kwargs={'reason': 'Only master node provides cluster stats'}
    )
    cluster_node_stats_handler = cluster_stub_handler
    cluster_processes_stats_handler = cluster_stub_handler
    cluster_proxies_stats_handler = cluster_stub_handler
    cluster_rabbitmq_stats_handler = cluster_stub_handler
    cluster_push_queue_stats_handler = cluster_stub_handler

  routes = {
    '/stats/cluster/nodes': cluster_node_stats_handler,
    '/stats/cluster/processes': cluster_processes_stats_handler,
    '/stats/cluster/proxies': cluster_proxies_stats_handler,
    '/stats/cluster/rabbitmq': cluster_rabbitmq_stats_handler,
    '/stats/cluster/push_queues': cluster_push_queue_stats_handler,
  }
  return [
    (route, handler.handler_class, handler.init_kwargs)
    for route, handler in routes.iteritems()
  ]


class ProfilingManager(object):
  """
  This manager watches stats profiling configs in Zookeeper,
  when configs are changed it starts/stops/restarts periodical
  tasks which writes profile log with proper parameters.
  """

  def __init__(self, zk_client):
    """ Initializes instance of ProfilingManager.
    Starts watching profiling configs in zookeeper.

    Args:
      zk_client: an instance of KazooClient - started zookeeper client.
    """
    self.nodes_profile_log = None
    self.processes_profile_log = None
    self.proxies_profile_log = None
    self.nodes_profile_task = None
    self.processes_profile_task = None
    self.proxies_profile_task = None

    def bridge_to_ioloop(update_function):
      """ Creates function which schedule execution of update_function
      inside current IOLoop.

      Args:
        update_function: a function to execute in IOLoop.
      Returns:
        A callable which schedules execution of update_function inside IOLoop.
      """
      def update_in_ioloop(new_conf, znode_stat):
        IOLoop.current().add_callback(update_function, new_conf, znode_stat)
      return update_in_ioloop

    zk_client.DataWatch(NODES_STATS_CONFIGS_NODE,
                        bridge_to_ioloop(self.update_nodes_profiling_conf))
    zk_client.DataWatch(PROCESSES_STATS_CONFIGS_NODE,
                        bridge_to_ioloop(self.update_processes_profiling_conf))
    zk_client.DataWatch(PROXIES_STATS_CONFIGS_NODE,
                        bridge_to_ioloop(self.update_proxies_profiling_conf))

  def update_nodes_profiling_conf(self, new_conf, znode_stat):
    """ Handles new value of nodes profiling configs and
    starts/stops profiling with proper parameters.

    Args:
      new_conf: a string representing new value of zookeeper node.
      znode_stat: an instance if ZnodeStat.
    """
    if not new_conf:
      logging.debug("No node stats profiling configs are specified yet")
      return
    logging.info("New nodes stats profiling configs: {}".format(new_conf))
    conf = json.loads(new_conf)
    enabled = conf["enabled"]
    interval = conf["interval"]
    if enabled:
      if not self.nodes_profile_log:
        self.nodes_profile_log = NodesProfileLog(DEFAULT_INCLUDE_LISTS)
      if self.nodes_profile_task:
        self.nodes_profile_task.stop()
      self.nodes_profile_task = _configure_profiling(
        stats_source=ClusterNodesStatsSource(),
        profiler=self.nodes_profile_log,
        interval=interval
      )
      self.nodes_profile_task.start()
    elif self.nodes_profile_task:
      self.nodes_profile_task.stop()
      self.nodes_profile_task = None

  def update_processes_profiling_conf(self, new_conf, znode_stat):
    """ Handles new value of processes profiling configs and
    starts/stops profiling with proper parameters.

    Args:
      new_conf: a string representing new value of zookeeper node.
      znode_stat: an instance if ZnodeStat.
    """
    if not new_conf:
      logging.debug("No processes stats profiling configs are specified yet")
      return
    logging.info("New processes stats profiling configs: {}".format(new_conf))
    conf = json.loads(new_conf)
    enabled = conf["enabled"]
    interval = conf["interval"]
    detailed = conf["detailed"]
    if enabled:
      if not self.processes_profile_log:
        self.processes_profile_log = ProcessesProfileLog(DEFAULT_INCLUDE_LISTS)
      self.processes_profile_log.write_detailed_stats = detailed
      if self.processes_profile_task:
        self.processes_profile_task.stop()
      self.processes_profile_task = _configure_profiling(
        stats_source=ClusterProcessesStatsSource(),
        profiler=self.processes_profile_log,
        interval=interval
      )
      self.processes_profile_task.start()
    elif self.processes_profile_task:
      self.processes_profile_task.stop()
      self.processes_profile_task = None

  def update_proxies_profiling_conf(self, new_conf, znode_stat):
    """ Handles new value of proxies profiling configs and
    starts/stops profiling with proper parameters.

    Args:
      new_conf: a string representing new value of zookeeper node.
      znode_stat: an instance if ZnodeStat.
    """
    if not new_conf:
      logging.debug("No proxies stats profiling configs are specified yet")
      return
    logging.info("New proxies stats profiling configs: {}".format(new_conf))
    conf = json.loads(new_conf)
    enabled = conf["enabled"]
    interval = conf["interval"]
    detailed = conf["detailed"]
    if enabled:
      if not self.proxies_profile_log:
        self.proxies_profile_log = ProxiesProfileLog(DEFAULT_INCLUDE_LISTS)
      self.proxies_profile_log.write_detailed_stats = detailed
      if self.proxies_profile_task:
        self.proxies_profile_task.stop()
      self.proxies_profile_task = _configure_profiling(
        stats_source=ClusterProxiesStatsSource(),
        profiler=self.proxies_profile_log,
        interval=interval
      )
      self.proxies_profile_task.start()
    elif self.proxies_profile_task:
      self.proxies_profile_task.stop()
      self.proxies_profile_task = None


def _configure_profiling(stats_source, profiler, interval):

  def write_stats_callback(future_stats):
    """ Gets stats from already finished future wrapper
    and calls profiler to write the stats.

    Args:
      future_stats: A Future wrapper for the cluster stats.
    """
    stats = future_stats.result()[0]  # result is a tuple (stats, failures)
    profiler.write(stats)

  def profiling_periodical_callback():
    """ Triggers asynchronous stats collection and schedules writing
    of the cluster stats (when it's collected) to the stats profile.
    """
    future_stats = stats_source.get_current_async(max_age=0)
    IOLoop.current().add_future(future_stats, write_stats_callback)

  return PeriodicCallback(profiling_periodical_callback, interval*1000)
