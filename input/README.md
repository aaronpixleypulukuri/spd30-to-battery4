# input/

Put your own files here. Everything in this folder except this README is ignored by git.

```
input/
  BACKUP01.BIN            your SPD-30 backup
  samples/
    001 Kit Name/
      Layer A/  1-KIK 001.wav, 2-SNR 004.wav, EXT1-TOM 002.wav, ...
      Layer B/  2-TOM 004.wav, ...
    002 Another Kit/
      ...
```

- Kit folders begin with the three-digit kit number and a space.
- WAV files begin with the pad prefix and a dash: `1-` … `8-` for pads 1–8, `EXT1-` … `EXT4-` for external triggers.
- WAVs must be PCM, 16- or 24-bit.

You can also keep your files anywhere and pass `--backup` and `--samples`.
