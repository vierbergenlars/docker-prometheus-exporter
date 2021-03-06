import prometheus_client
import docker
import logging
import os
import threading
from time import sleep

logging.basicConfig(level=logging.INFO)

client = docker.from_env()
prometheus_client.start_http_server(8080)
monitor_label = os.environ.get('MONITOR_LABEL', 'be.vbgn.prometheus-docker-exporter')
prometheus_prefix = os.environ.get('STATS_PREFIX', 'docker_container_')

expose_labels = [l.strip() for l in os.environ.get('EXPOSE_LABELS', '').split(',') if l != '']
labels = ['name', ] + ['label_'+l.replace('.', '_') for l in expose_labels]
refresh_interval = int(os.environ.get('REFRESH_INTERVAL', 10))
metrics = {}

def calculate_cpu_usage(stats, metric):
    try:
        cpu_delta = stats['cpu_stats']['cpu_usage'][metric] - stats['precpu_stats']['cpu_usage'][metric]
        system_delta = stats['cpu_stats']['system_cpu_usage'] - stats['precpu_stats']['system_cpu_usage']
    except KeyError:
        logging.exception('Error calculating cpu usage')
        return 0.0

    if system_delta > 0 and cpu_delta > 0:
        return cpu_delta / system_delta
    return 0.0

def calculate_blkio(stats, metric, op):
    val = 0
    for blkstat in stats['blkio_stats'][metric]:
        if blkstat['op'].lower() == op.lower():
            val += blkstat['value']
    return val


class ContainerStatsThread(threading.Thread):
    def __init__(self, container_id):
        super().__init__(name='Stats#'+container_id, daemon=True)
        self.container_id = container_id
        self.stop = threading.Event()

    def run(self):
        container = client.containers.get(self.container_id)
        metric_labels = get_container_metric_labels(container)
        logging.info('Stats thread for %s: labels %r', container.id, metric_labels)
        for stats in container.stats(decode=True, stream=True):
            logging.debug('Got stats for %s: %r', container.id, stats)
            if self.stop.is_set():
                log_metric('pids', metric_labels, 0)
                log_metric('cpu_usage_total', metric_labels, 0)
                log_metric('cpu_usage_system', metric_labels, 0)
                log_metric('cpu_usage_user', metric_labels, 0)
                log_metric('memory_usage', metric_labels, 0)
                logging.info('Stopped statistics thread for %s', container.id)
                return
            log_metric('pids', metric_labels, stats['pids_stats']['current'])
            log_metric('cpu_usage_total', metric_labels, calculate_cpu_usage(stats, 'total_usage'))
            log_metric('cpu_usage_system', metric_labels, calculate_cpu_usage(stats, 'usage_in_kernelmode'))
            log_metric('cpu_usage_user', metric_labels, calculate_cpu_usage(stats, 'usage_in_usermode'))
            log_metric('memory_usage', metric_labels, stats['memory_stats']['usage'])
            log_metric('memory_usage_max', metric_labels, stats['memory_stats']['max_usage'])
            log_metric('memory_limit', metric_labels, stats['memory_stats']['limit'])
            for dev, devstats in stats['networks'].items():
                for statname,value in devstats.items():
                    log_metric('net_'+statname, metric_labels, value, {'network_interface': dev})

            for typ in ['read', 'write', 'sync', 'async', 'total']:
                log_metric('io_bytes_'+typ, metric_labels, calculate_blkio(stats, 'io_service_bytes_recursive', typ))
                log_metric('io_ops_'+typ, metric_labels, calculate_blkio(stats, 'io_serviced_recursive', typ))




def get_metric(name, extra_labels):
    try:
        return metrics[name]
    except KeyError:
        metrics[name] = prometheus_client.Gauge(prometheus_prefix + name, name, labels+list(extra_labels.keys()))
        return metrics[name]

def log_metric(name, labels, value, extra_labels={}):
    logging.debug('Publish metric %s, %r, %r', name, labels, value)
    get_metric(name, extra_labels).labels(**labels, **extra_labels).set(value)

def get_container_metric_labels(container):
    base = {
        'name': container.name,
    }
    for label in expose_labels:
        base['label_'+label.replace('.', '_')] = container.labels.get(label, '')
    return base

stats_threads = {}

while True:
    containers = client.containers.list(filters={'label': monitor_label})
    container_ids = [container.id for container in containers]

    for container_id in container_ids:
        if container_id not in stats_threads:
            logging.info('Creating statistics thread for container %s', container_id)
            stats_threads[container_id] = ContainerStatsThread(container_id)
            stats_threads[container_id].start()

    logging.debug('Stats threads after create: %r', stats_threads)

    for ctid, thread in stats_threads.items():
        if ctid not in container_ids:
            logging.info('Container %s is no longer active. Stopping statistics thread %r', ctid, thread)
            thread.stop.set()
            stats_threads[ctid] = None

    stats_threads = {ctid: thread for ctid, thread in stats_threads.items() if thread is not None}

    logging.debug('Stats threads after prune: %r', stats_threads)

    sleep(refresh_interval)


