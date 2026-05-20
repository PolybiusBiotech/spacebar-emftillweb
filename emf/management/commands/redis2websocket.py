# This module implements a command line program that connects to redis
# and accepts websocket connections; websocket clients can subscribe
# to redis keys and changes to those keys will be sent as websocket
# messages.

# It is expected that a single instance of this program will be run as
# a daemon. In production this will be proxied behind nginx.

from django.core.management.base import BaseCommand
import asyncio
import websockets
import json
import redis.asyncio as redis
import sdnotify
# import re

nke = "notify-keyspace-events"

# We may add more keys in the future, and we don't want to have to change this
# so ignore for now
# keyre = re.compile(r'(^(stockline|stocktype|stockitem)/\d+$)|(^totals/.*$)')

rclient = None

# Dict of redis key to set of subscriber websockets
subscriptions = {}


def error(message):
    return json.dumps({
        "type": "error",
        "message": message,
    })


def not_present(key):
    return json.dumps({
        "type": "not present",
        "key": key,
    })


async def subscribe(websocket, key):
    # if keyre.match(key) is None:
    #     await websocket.send(error("Invalid key"))
    #     return
    subscribers = subscriptions.setdefault(key, set())
    subscribers.add(websocket)
    current_value = await rclient.get(key)
    if not current_value:
        current_value = not_present(key)
    await websocket.send(current_value)


def unsubscribe(websocket, key):
    subscribers = subscriptions.get(key)
    if not subscribers:
        return
    subscribers.discard(websocket)


async def handler(websocket):
    try:
        async for message in websocket:
            if not message:
                continue
            if len(message) > 80:
                await websocket.send(error("Request too long"))
                continue
            x = message.split()
            if len(x) != 2:
                await websocket.send(error("Invalid request"))
                continue
            if x[0].lower() == "subscribe":
                await subscribe(websocket, x[1])
            elif x[0].lower() == "unsubscribe":
                unsubscribe(websocket, x[1])
            else:
                await websocket.send(
                    error("Only SUBSCRIBE and UNSUBSCRIBE are supported"))
    except websockets.exceptions.ConnectionClosedError:
        # websockets.exceptions.ConnectionClosedError is raised if the
        # TCP connection was closed without being shut down properly
        # with a close frame.  We can ignore it.
        pass
    except websockets.exceptions.ConnectionClosedOK:
        pass
    finally:
        # Now remove all subscriptions!
        for s in subscriptions.values():
            s.discard(websocket)


async def amain(options):
    global rclient
    rclient = redis.Redis(
        host=options["redis_host"], port=options["redis_port"],
        decode_responses=True)
    # Check redis config includes notify-keyspace-events letters 'K' and 'A'
    cfg = (await rclient.config_get(nke))[nke]
    if 'E' not in cfg or 'A' not in cfg:
        await rclient.config_set(nke, 'EA')

    async with rclient.pubsub(ignore_subscribe_messages=True) as pubsub:
        await pubsub.psubscribe("__keyevent@*__:*")
        async with websockets.serve(
                handler,
                None if not options["bind"] else options["bind"],
                options["port"]):
            # NOW we can report to systemd that we are ready
            # (this won't block)
            sdnotify.SystemdNotifier().notify("READY=1")
            async for message in pubsub.listen():
                key = message['data']
                subscribers = subscriptions.get(key)
                if subscribers:
                    value = await rclient.get(key)
                    if value:
                        websockets.broadcast(subscribers, value)
                    else:
                        websockets.broadcast(subscribers, not_present(key))


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

    def handle(self, *args, **options):
        asyncio.run(amain(options))
