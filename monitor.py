import prometheus_client
import docker
import logging
import os
from time import sleep

logging.basicConfig(level=logging.DEBUG)

client = docker.from_env()
prometheus_client.start_http_server(8080)
monitor_label = os.environ.get('MONITOR_LABEL', 'be.vbgn.prometheus-docker-exporter')
prometheus_prefix = os.environ.get('STATS_PREFIX', 'docker_container_')

expose_labels = [l.trim() for l in os.environ.get('EXPOSE_LABELS', '').split(',') if l != '']
labels = ['name', ] + ['label_'+l for l in expose_labels]
metrics = {}

def get_metric(name, extra_labels):
    try:
        return metrics[name]
    except KeyError:
        metrics[name] = prometheus_client.Gauge(prometheus_prefix + name, name, labels+list(extra_labels.keys()))
        return metrics[name]

def log_metric(name, labels, value, extra_labels={}):
    get_metric(name, extra_labels).labels(**labels, **extra_labels).set(value)

def get_container_metric_labels(container):
    base = {
        'name': container.name,
    }
    for label in expose_labels:
        base['label_'+label] = container.labels[label]
    return base


while True:

    containers = client.containers.list(filters={'label': monitor_label})

    for container in containers:
        stats = container.stats(stream=False, decode=True)
        metric_labels = get_container_metric_labels(container)

        log_metric('pids', metric_labels, stats['pids_stats']['current'])
        log_metric('cpu_usage_total', metric_labels, stats['cpu_stats']['cpu_usage']['total_usage'])
        log_metric('cpu_usage_system', metric_labels, stats['cpu_stats']['cpu_usage']['usage_in_kernelmode'])
        log_metric('cpu_usage_user', metric_labels, stats['cpu_stats']['cpu_usage']['usage_in_usermode'])
        log_metric('memory_usage', metric_labels, stats['memory_stats']['usage'])
        log_metric('memory_usage_max', metric_labels, stats['memory_stats']['max_usage'])
        log_metric('memory_limit', metric_labels, stats['memory_stats']['limit'])
        for dev, devstats in stats['networks'].items():
            for statname,value in devstats.items():
                log_metric('net_'+statname, metric_labels, value, {'network_interface': dev})

        
        logging.debug('Stats for container %s: %r', container.id, stats)
    
    sleep(int(os.environ.get('REFRESH_INTERVAL', 10)))


