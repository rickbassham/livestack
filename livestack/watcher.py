import logging
from pathlib import Path
import time
from typing import Union, Callable

from watchdog.observers.polling import PollingObserver as Observer
from watchdog.events import FileSystemEventHandler, FileCreatedEvent, DirCreatedEvent


class Handler(FileSystemEventHandler):
    def __init__(self, callback: Callable[[str], None]):
        self.callback = callback

    def on_created(self, event: Union[FileCreatedEvent, DirCreatedEvent]):
        if event.is_directory:
            return

        if not event.src_path.endswith(".fits"):
            return

        logging.info(f"file created: {event.src_path}")

        time.sleep(10)  # give the file time to be written

        self.callback(event.src_path)


class Watcher:
    def __init__(self, callback: Callable[[str], None]):
        self.observer = Observer()
        self.callback = callback

    def run(self, dir: str):
        event_handler = Handler(self.callback)
        self.observer.schedule(event_handler, dir, recursive=True)
        self.observer.start()

        files = list(Path(dir).rglob("*.fits"))
        files.sort()

        for f in files:
            self.callback(str(f))

    def stop(self):
        self.observer.stop()
        self.observer.join()
