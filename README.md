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
