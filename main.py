import base64
import functools
import json
import logging
import logging.handlers
import os
import sys
import time
from queue import SimpleQueue as Queue, Empty
from typing import List

import asyncio
import websockets
import png

from livestack.watcher import Watcher
from livestack.stacking_service import Stacker
from livestack.utils import GracefulSignalHandler

logging.basicConfig(level=logging.INFO)


class LocalQueueHandler(logging.handlers.QueueHandler):
    def emit(self, record: logging.LogRecord) -> None:
        # Removed the call to self.prepare(), handle task cancellation
        try:
            self.enqueue(record)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.handleError(record)


async def server(ws: websockets.WebSocketServerProtocol, path, stacker: Stacker = None):
    queue: Queue = Queue()
    logging.getLogger().addHandler(LocalQueueHandler(queue))

    output_queue: Queue = Queue()
    if stacker:
        queue_id = stacker.add_output_queue(output_queue)

    while not ws.closed:
        try:
            f = output_queue.get(block=False)

            with open(f, "br") as img:
                encoded = base64.b64encode(img.read())

            await ws.send(
                json.dumps(
                    {
                        "type": "livestack_image",
                        "payload": "data:image/png;base64," + encoded.decode("ascii"),
                    }
                )
            )
        except Empty:
            pass

        try:
            record = queue.get(block=False)

            await ws.send(
                json.dumps(
                    {
                        "type": "livestack_log",
                        "payload": record.getMessage(),
                    }
                )
            )
        except Empty:
            await asyncio.sleep(0.5)

    await ws.wait_closed()

    if stacker:
        stacker.remove_output_queue(queue_id)


async def stacker(s: Stacker):
    s.start()

    w = Watcher(s.stack_image)
    w.run(os.environ["INPUT_FOLDER"])

    with GracefulSignalHandler() as h:
        while not h.signaled:
            await asyncio.sleep(1)

    w.stop()
    logging.info("watcher stopped")
    s.stop()
    logging.info("stacker stopped")

    loop = asyncio.get_event_loop()

    loop.stop()


if __name__ == "__main__":
    s = Stacker(
        os.environ["STORAGE_FOLDER"],
        os.environ["OUTPUT_FOLDER"],
    )

    bound_handler = functools.partial(server, stacker=s)
    start_server = websockets.serve(bound_handler, "0.0.0.0", 5678)
    asyncio.get_event_loop().run_until_complete(start_server)

    asyncio.ensure_future(stacker(s))
    asyncio.get_event_loop().run_forever()
