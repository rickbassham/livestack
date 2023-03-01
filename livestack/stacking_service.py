import logging
import math
import simplejson as json
import os
from os.path import join, isfile
from queue import Queue, Empty
from threading import Thread
from typing import Optional, Tuple, List, Dict
import uuid

import astroalign as aa
from astropy.io import fits
from astropy.io.fits import ImageHDU, HDUList, Header, Card, PrimaryHDU
import numpy as np
import png
from skimage import filters, transform, exposure
from auto_stretch.stretch import Stretch
from PIL import Image as PILImage, ImageEnhance
from colour_demosaicing import demosaicing_CFA_Bayer_bilinear

from .utils import Timer

DEBUG = os.getenv("DEBUG", "") == "1"


def crop_center(img, cropx, cropy):
    y, x = img.shape
    startx = x // 2 - (cropx // 2)
    starty = y // 2 - (cropy // 2)
    return img[starty : starty + cropy, startx : startx + cropx]


class Image:
    def __init__(self, img: ImageHDU):
        self.subcount = 1

        hdr = img.header
        bitpix = int(hdr["BITPIX"])

        if bitpix > 0:
            self.data = np.float32(
                np.interp(img.data, (0, int(math.pow(2, bitpix)) - 1), (0, 1))
            )
        else:  # fits stores bitpix as -32 or -64 for floating point data
            self.data = np.float32(img.data)

        if DEBUG:
            assert (
                self.data.dtype == np.float32
                and self.data.max() <= 1.0
                and self.data.min() >= 0.0
            ), f"{self.data.dtype} {self.data.max()} {self.data.min()}"

        self.bayer_pattern = hdr.get("BAYERPAT", None)

        self.camera = hdr["INSTRUME"]
        self.exp = round(float(hdr["EXPTIME"]), 2)
        self.gain = hdr.get("GAIN", 0)
        # round temp to the nearest 5 degrees
        self.temp = 5 * round(float(hdr["CCD-TEMP"]) / 5)

        image_type = hdr["IMAGETYP"]

        self.subcount = hdr.get("SUBCOUNT") or 1

        if str(image_type).lower().find("light") >= 0:
            self.image_type = "LIGHT"
            self.target = hdr["OBJECT"]
            self.filter = hdr.get("FILTER", "NONE")

        elif str(image_type).lower().find("dark") >= 0:
            self.image_type = "DARK"
            self.target = None
            self.filter = None

        elif str(image_type).lower().find("flat") >= 0:
            self.image_type = "FLAT"
            self.filter = hdr.get("FILTER", "NONE")
            self.target = None

        else:
            self.image_type = "UNKNOWN"

    def __iter__(self):
        yield "camera", self.camera
        yield "exp", self.exp
        yield "gain", self.gain
        yield "temp", self.temp
        yield "image_type", self.image_type
        yield "target", self.target
        yield "filter", self.filter
        yield "key", self.key
        yield "dark_key", self.dark_key
        yield "flat_key", self.flat_key

        if self.bayer_pattern:
            yield "bayer_pattern", self.bayer_pattern

    @property
    def key(self) -> Optional[str]:
        if self.image_type == "LIGHT":
            return f"{self.camera}_{self.image_type}_{self.target}_{self.filter}_{self.exp}_{self.gain}_{self.temp}"
        elif self.image_type == "DARK":
            return f"{self.camera}_{self.image_type}_{self.exp}_{self.gain}_{self.temp}"
        elif self.image_type == "FLAT":
            return (
                f"{self.camera}_{self.image_type}_{self.filter}_{self.gain}_{self.temp}"
            )
        return None

    @property
    def dark_key(self) -> Optional[str]:
        if self.image_type == "LIGHT" or self.image_type == "FLAT":
            return f"{self.camera}_DARK_{self.exp}_{self.gain}_{self.temp}"
        return None

    @property
    def flat_key(self) -> Optional[str]:
        if self.image_type == "LIGHT":
            return f"{self.camera}_FLAT_{self.filter}_{self.gain}_{self.temp}"
        return None

    @property
    def fits_header(self) -> Header:
        hdr = Header()

        hdr.set("INSTRUME", self.camera)
        hdr.set("EXPTIME", self.exp)
        hdr.set("GAIN", self.gain)
        hdr.set("CCD-TEMP", self.temp)

        if self.image_type == "LIGHT":
            hdr.set("IMAGETYP", "Light Frame")
            hdr.set("OBJECT", self.target)
            hdr.set("FILTER", self.filter)
        elif self.image_type == "FLAT":
            hdr.set("IMAGETYP", "Flat Frame")
            hdr.set("FILTER", self.filter)
        elif self.image_type == "DARK":
            hdr.set("IMAGETYP", "Dark Frame")

        hdr.set("SUBCOUNT", self.subcount)

        return hdr

    def save_fits(self, folder: str) -> str:
        data = self.data.copy()

        if DEBUG:
            assert (
                data.dtype == np.float32 and data.max() <= 1.0 and data.min() >= 0.0
            ), f"{data.dtype} {data.max()} {data.min()}"

        hdu = PrimaryHDU(
            data=self.data,
            header=self.fits_header,
        )
        l = HDUList([hdu])
        path = join(folder, f"{self.key}.fits")
        l.writeto(path, overwrite=True)
        return path

    def save_stretched_png(self, folder: str) -> str:
        data = self.data.copy()
        path = join(folder, f"{self.key}.png")

        if DEBUG:
            assert (
                data.dtype == np.float32 and data.max() <= 1.0 and data.min() >= 0.0
            ), f"{data.dtype} {data.max()} {data.min()}"

        if data.ndim == 2:  # mono
            data = crop_center(data, data.shape[1] - 128, data.shape[0] - 128)

            with Timer("stretch"):
                data = Stretch().stretch(data)

            data = transform.downscale_local_mean(data, (4, 4))
            data = np.clip(data, 0.0, 1.0)

            if DEBUG:
                assert (
                    data.dtype == np.float32 and data.max() <= 1.0 and data.min() >= 0.0
                ), f"{data.dtype} {data.max()} {data.min()}"

            with Timer(f"saving {self.key}.png"):
                scaled = np.interp(data, (0, 1), (0, 65535)).astype(np.uint16)

                png_image = PILImage.fromarray(scaled)
                png_image.save(path)
        elif data.ndim == 3:  # osc
            channels = []

            for i in range(3):
                channel_data = data[i]
                channel_data = crop_center(
                    channel_data,
                    channel_data.shape[1] - 128,
                    channel_data.shape[0] - 128,
                )

                with Timer("stretch"):
                    channel_data = Stretch().stretch(channel_data)

                channel_data = transform.downscale_local_mean(channel_data, (4, 4))
                channel_data = np.clip(channel_data, 0.0, 1.0)

                if DEBUG:
                    assert (
                        channel_data.dtype == np.float32
                        and channel_data.max() <= 1.0
                        and channel_data.min() >= 0.0
                    ), f"{channel_data.dtype} {channel_data.max()} {channel_data.min()}"

                # channel_data = np.interp(channel_data, (0.0, 1.0), (0, 255)).astype(np.uint8)

                channels.append(channel_data)

            final = np.dstack(channels)
            final = exposure.equalize_adapthist(final, clip_limit=0.0001, nbins=1024)

            final = np.interp(final, (0.0, 1.0), (0, 255)).astype(np.uint8)

            with Timer(f"saving {self.key}.png"):
                if DEBUG:
                    assert final.dtype == np.uint8

                png_image = PILImage.fromarray(final, mode="RGB")
                converter = ImageEnhance.Color(png_image)
                saturated = converter.enhance(1.6)  # increase color saturation
                saturated.save(path)

            pass
        else:
            raise Exception(f"invalid image dimensions {data.ndim}")

        return path


class DB:
    def __init__(self, folder: str):
        self.folder = folder
        self.processed: List[str] = []

        if isfile(join(folder, "processed.txt")):
            with open(join(folder, "processed.txt")) as f:
                self.processed = [line.rstrip() for line in f]

    def is_already_processed(self, path: str) -> bool:
        return path in self.processed

    def stack_exists(self, img: Image) -> bool:
        return os.path.isfile(join(self.folder, f"{img.key}.fits"))

    def mark_processed(self, path: str):
        self.processed.append(path)
        with open(join(self.folder, "processed.txt"), "a+") as f:
            f.write(f"{path}\n")
            f.flush()

    def get_stacked_image(self, key: str) -> Optional[Image]:
        try:
            with fits.open(join(self.folder, f"{key}.fits")) as f:
                return Image(f[0])
        except:
            return None


class Stacker:
    def __init__(self, storage_folder: str, output_folder: str):
        self.storage_folder = storage_folder
        self.output_folder = output_folder
        self.queue: Queue = Queue()
        self.thread = None
        self.db = DB(self.storage_folder)
        self._stop = False
        self.output_queues: Dict[str, Queue] = {}

        os.makedirs(self.storage_folder, exist_ok=True)
        os.makedirs(self.output_folder, exist_ok=True)

    def add_output_queue(self, q: Queue) -> str:
        id = str(uuid.uuid4())
        self.output_queues[id] = q
        return id

    def remove_output_queue(self, id: str):
        del self.output_queues[id]

    def start(self):
        if self.thread:
            return

        self.thread = Thread(target=self._worker)
        self.thread.start()

    def stop(self):
        self._stop = True
        self.thread.join()

    def stack_image(self, path: str):
        self.queue.put(path)

    def _process_item(self, path: str):
        if self.db.is_already_processed(path):
            logging.info(f"skipping already processed file {path}")
            return

        with Timer(f"processing file {path}"):
            with fits.open(path) as fit:
                img = Image(fit[0])

            # always mark it as processed. if we error out, we don't want to keep
            # erroring on the same file
            self.db.mark_processed(path)

            if img.image_type == "LIGHT":
                img = self._subtract_dark(img)
                img = self._divide_flat(img)

                if img.bayer_pattern:
                    img = self._debayer(img)

                img = self._align(img)
                stacked = self._stack(img)

                png_path = stacked.save_stretched_png(self.output_folder)
                for q in self.output_queues.values():
                    q.put(png_path)

            elif img.image_type == "DARK":
                self._stack(img)
            elif img.image_type == "FLAT":
                img = self._subtract_dark(img)
                self._stack(img)
            else:
                logging.info("skipping file with unknown IMAGETYP header")

    def _subtract_dark(self, img: Image) -> Image:
        dark = self.db.get_stacked_image(str(img.dark_key))
        if dark is None:
            logging.info(f"no dark found for {img.dark_key}")
            return img

        if DEBUG:
            assert (
                dark.data.dtype == np.float32
                and dark.data.min() >= 0.0
                and dark.data.max() <= 1.0
            ), f"{dark.data.dtype} {dark.data.max()} {dark.data.min()}"
            assert (
                img.data.dtype == np.float32
                and img.data.min() >= 0.0
                and img.data.max() <= 1.0
            ), f"{img.data.dtype} {img.data.max()} {img.data.min()}"
            assert (
                dark.data.shape == img.data.shape
            ), f"{dark.data.shape} {img.data.shape}"

        with Timer(f"subtracting dark for {img.dark_key}"):
            img.data = img.data - dark.data
            img.data = np.clip(img.data, 0.0, 1.0)

        return img

    def _divide_flat(self, img: Image) -> Image:
        flat = self.db.get_stacked_image(str(img.flat_key))
        if flat is None:
            logging.info(f"no flat found for {img.flat_key}")
            return img

        if DEBUG:
            assert (
                flat.data.dtype == np.float32
                and flat.data.min() >= 0.0
                and flat.data.max() <= 1.0
            ), f"{flat.data.dtype} {flat.data.max()} {flat.data.min()}"
            assert (
                img.data.dtype == np.float32
                and img.data.min() >= 0.0
                and img.data.max() <= 1.0
            ), f"{img.data.dtype} {img.data.max()} {img.data.min()}"
            assert (
                flat.data.shape == img.data.shape
            ), f"{flat.data.shape} {img.data.shape}"

        with Timer(f"dividing flat for {img.flat_key}"):
            img.data = img.data / (flat.data / flat.data.mean())
            img.data = np.clip(img.data, 0.0, 1.0)

        return img

    def _debayer(self, img: Image) -> Image:
        if DEBUG:
            assert (
                img.data.dtype == np.float32
                and img.data.min() >= 0.0
                and img.data.max() <= 1.0
            ), f"{img.data.dtype} {img.data.max()} {img.data.min()}"

        if not img.bayer_pattern:
            logging.warn(f"no bayer pattern detected for {img.key}")
            return img

        with Timer(f"debayering image for {img.key} with pattern {img.bayer_pattern}"):
            data = demosaicing_CFA_Bayer_bilinear(img.data, pattern=img.bayer_pattern)

            # fix the image shape
            img.data = np.array([data[:, :, 0], data[:, :, 1], data[:, :, 2]])
            img.data = np.interp(img.data, (0.0, img.data.max()), (0.0, 1.0)).astype(
                np.float32
            )

            # poor man's SCNR
            # img.data[1] *= 0.8

        if DEBUG:
            assert (
                img.data.dtype == np.float32
                and img.data.min() >= 0.0
                and img.data.max() <= 1.0
            ), f"{img.data.dtype} {img.data.max()} {img.data.min()}"

        return img

    def _align(self, img: Image) -> Image:
        reference = self.db.get_stacked_image(str(img.key))

        if reference is None:
            logging.info(f"no reference found for {img.key}")
            return img

        if DEBUG:
            assert (
                img.data.dtype == np.float32
                and img.data.min() >= 0.0
                and img.data.max() <= 1.0
            ), f"{img.data.dtype} {img.data.max()} {img.data.min()}"
            assert (
                reference.data.dtype == np.float32
                and reference.data.min() >= 0.0
                and reference.data.max() <= 1.0
            ), f"{reference.data.dtype} {reference.data.max()} {reference.data.min()}"
            assert (
                reference.data.ndim == img.data.ndim
            ), f"{reference.data.ndim} {img.data.ndim}"
            assert (
                reference.data.shape == img.data.shape
            ), f"{reference.data.shape} {img.data.shape}"

        with Timer(f"aligning image for {img.key}"):
            if img.data.ndim == 2:
                registered, footprint = aa.register(
                    img.data, reference.data, fill_value=0.0
                )
                img.data = registered
            elif img.data.ndim == 3:
                transform, _ = aa.find_transform(img.data[0], reference.data[0])

                for i in range(3):
                    transformed, _ = aa.apply_transform(
                        transform, img.data[i], reference.data[i], fill_value=0.0
                    )
                    img.data[i] = transformed
            else:
                raise Exception(f"invalid image dimensions {img.data.ndim}")

        if DEBUG:
            assert (
                img.data.dtype == np.float32
                and img.data.min() >= 0.0
                and img.data.max() <= 1.0
            ), f"{img.data.dtype} {img.data.max()} {img.data.min()}"

        return img

    def _stack(self, img: Image) -> Image:
        if DEBUG:
            assert (
                img.data.dtype == np.float32
                and img.data.min() >= 0.0
                and img.data.max() <= 1.0
            ), f"{img.data.dtype} {img.data.max()} {img.data.min()}"

        stacked = self.db.get_stacked_image(str(img.key))

        if stacked is None:
            logging.info(f"no reference found for {img.key}")
            stacked = img
            stacked.subcount = 0
        else:
            if DEBUG:
                assert (
                    stacked.data.dtype == np.float32
                    and stacked.data.min() >= 0.0
                    and stacked.data.max() <= 1.0
                ), f"{stacked.data.dtype} {stacked.data.max()} {stacked.data.min()}"
                assert (
                    stacked.data.ndim == img.data.ndim
                ), f"{stacked.data.ndim} {img.data.ndim}"
                assert (
                    stacked.data.shape == img.data.shape
                ), f"{stacked.data.shape} {img.data.shape}"

            with Timer(f"stacking image for {img.key}"):
                if img.image_type == "LIGHT":
                    data = img.data
                else:
                    data = filters.gaussian(img.data)

                if DEBUG:
                    assert (
                        img.data.dtype == np.float32
                        and img.data.min() >= 0.0
                        and img.data.max() <= 1.0
                    ), f"{img.data.dtype} {img.data.max()} {img.data.min()}"

                count = stacked.subcount

                stacked.data = (count * stacked.data + data) / (count + 1)

        if DEBUG:
            assert (
                stacked.data.dtype == np.float32
                and stacked.data.min() >= 0.0
                and stacked.data.max() <= 1.0
            ), f"{stacked.data.dtype} {stacked.data.max()} {stacked.data.min()}"

        stacked.subcount += 1

        with Timer(f"saving stacked fits for {stacked.key}"):
            stacked.save_fits(self.storage_folder)

        return stacked

    def _worker(self):
        while not self._stop:
            try:
                item = self.queue.get(timeout=1)
            except Empty:
                continue

            try:
                self._process_item(item)
            except Exception as e:
                logging.error(e)
                raise
            finally:
                self.queue.task_done()

            logging.info(f"{self.queue.qsize()} items remaining")
