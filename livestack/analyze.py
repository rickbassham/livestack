from pathlib import Path
from astropy.io import fits
from astropy.io.fits import ImageHDU, HDUList, Header, Card, PrimaryHDU

def analyze_files(dir):
    files = list(Path(dir).rglob("*.fits"))
    files.sort()

    for filePath in files:
        with fits.open(filePath) as f:
            hdr: Header = f[0].header

            print(filePath)
            print(hdr.tostring("\n"))
