# livestack

A stand-alone, background stacker for astro images. Will do dark subtraction,
flat calibration, alignment, and stacking of images.

# Quick Start with Docker

First install Docker and docker-compose.

https://docs.docker.com/get-docker/

https://docs.docker.com/compose/install/

```
git clone https://github.com/rickbassham/livestack.git
cd livestack
nano docker-compose.yml
```

Replace `./data/input` with the location your imaging software places new exposures.
DO NOT change the `:/livestack/input` part of the line.
Save and exit.

```
docker-compose build
docker-compose up
```

As images are added to the input folder, they will be stacked, auto stretched,
and converted to png for viewing. The png files will be stored in the output folder.

You can browse the files via the web browser by visiting http://localhost:8080.

# Alpha Software

This is very much alpha software at this point. One Shot Color images are not
supported at all, and will likely break in weird ways.

This should not modify your original files in any way, however.

There are no options to configure, other than the folders used.

# How it works

When fits images are added to the input folder, we queue them up to be processed.
The service will look at the `IMAGETYP` fits header to determine if it is a light,
dark, or flat frame and process it accordingly.

Flat and Dark frames are processed by a simple mean calculation after applying
a guassian blur. Flat frames will have a dark frame subtracted if the service can
find a matching dark frame. Matches are done by comparing the following fits
keywords: `INSTRUME`, `EXPTIME`, `GAIN`, `CCD-TEMP`.

Light frames are dark subtracted (if we can find a matching dark) and flat
calibrated. Flats are matched on the following fits keywords: `INSTRUME`, `FILTER`,
`GAIN`, `CCD-TEMP`. Then the light frames are aligned to an existing stack, if found.
These are matched using the following fits keywords: `INSTRUME`, `FILTER`,
`GAIN`, `CCD-TEMP`, `OBJECT`, `EXPTIME`. Once aligned, they are stacked using a
simple mean algorithm, then a PNG file is created by auto-stretching the midtones
of the image.

The service can be restarted and will remember which files it has processed.

It is recommended to do darks and flats first, so the stack of lights will be of
higher quality.

If a stack is created poorly for some reason, you can remove it from the storage
folder. The list of processed files is stored in the storage folder. If you delete
this list, it the service will forget which files have been processed, and will
reprocess them.
