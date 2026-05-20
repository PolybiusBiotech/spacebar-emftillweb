# This module implements a command line program that connects to redis
# and a mqtt server, and forwards key changes in redis to mqtt topics

# It is expected that a single instance of this program will be run as
# a daemon.

from django.core.management.base import BaseCommand
import argparse
import asyncio
import json
import redis.asyncio as redis
import sdnotify
import sys
from gmqtt import Client as MQTTClient

nke = "notify-keyspace-events"

rclient = None
mclient = None


def on_mqtt_disconnect(client, packet, exc=None):
    # Cleanup? Who needs cleanup!
    sys.exit(1)


async def amain(options, mqtt_password):
    global rclient, mclient

    prefix = options["mqtt_topic_prefix"]
    retain = options["mqtt_retain"]

    rclient = redis.Redis(
        host=options["redis_host"], port=options["redis_port"],
        decode_responses=True)
    mclient = MQTTClient(options["mqtt_client_id"])
    # Check redis config includes notify-keyspace-events letters 'K' and 'A'
    cfg = (await rclient.config_get(nke))[nke]
    if 'E' not in cfg or 'A' not in cfg:
        await rclient.config_set(nke, 'EA')

    if options["mqtt_username"]:
        mclient.set_auth_credentials(options["mqtt_username"], mqtt_password)

    mclient.on_disconnect = on_mqtt_disconnect
    await mclient.connect(options["mqtt_host"], port=options["mqtt_port"],
                          ssl=options["mqtt_tls"])

    # Init - dump all redis keys to mqtt
    # Potentially racy (we don't spot changes while we are doing this)
    # but startup is a rare event and as keys change we will eventually
    # catch up
    async for key in rclient.scan_iter("*"):
        value = await rclient.get(key)
        mclient.publish(prefix + key, payload=value, retain=retain)

    async with rclient.pubsub(ignore_subscribe_messages=True) as pubsub:
        await pubsub.psubscribe("__keyevent@*__:*")
        sdnotify.SystemdNotifier().notify("READY=1")
        async for message in pubsub.listen():
            key = message['data']
            value = await rclient.get(key)
            if value:
                mclient.publish(prefix + key, payload=value, retain=options[
                    "mqtt_retain"])
            else:
                if retain:
                    # Delete previously retained payload
                    mclient.publish(prefix + key, retain=True)
                mclient.publish(
                    prefix + key, retain=False, payload=json.dumps(
                        {
                            "type": "not present",
                            "key": key,
                        }, indent=2))


class Command(BaseCommand):
    help = 'Forward events from redis to websocket clients'

    def add_arguments(self, parser):
        parser.add_argument("--bind", "-b", type=str, action="append",
                            help="Host to bind to")
        parser.add_argument("--port", "-p", type=int, action="store",
                            help="Port to listen on", default=8001)
        parser.add_argument(
            '--redis-host', action='store', default='localhost',
            help="Host to use to access redis")
        parser.add_argument(
            '--redis-port', action='store', default=6379, type=int,
            help="Port to use to access redis")
        parser.add_argument(
            '--mqtt-client-id', action='store', default="emf-bar",
            help="MQTT client ID")
        parser.add_argument(
            '--mqtt-host', action='store', default='localhost',
            help="MQTT broker hostname")
        parser.add_argument(
            '--mqtt-port', action='store', type=int, default=1883,
            help="MQTT broker port")
        parser.add_argument(
            '--mqtt-tls', action='store_true', default=False,
            help="Connect to MQTT broker using TLS")
        parser.add_argument(
            '--mqtt-username', action='store', default=None,
            help="MQTT broker username")
        parser.add_argument(
            '--mqtt-password-file', type=argparse.FileType('r'),
            default=None)
        parser.add_argument(
            '--mqtt-retain', action='store_true', default=False,
            help="Set the MQTT Retain flag")
        parser.add_argument(
            '--mqtt-topic-prefix', action='store', default='emf/bar/',
            help="Prefix to add to keys to make MQTT topics")

    def handle(self, *args, **options):
        mqtt_password = options["mqtt_password_file"].read().strip() \
            if options["mqtt_password_file"] else None
        asyncio.run(amain(options, mqtt_password))
