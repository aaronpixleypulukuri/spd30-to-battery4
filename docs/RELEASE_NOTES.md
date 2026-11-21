# Release notes

## v0.1.0 — first public release (experimental)

First public version of the SPD-30 to Battery 4 converter.

**What it does**
- Converts all kits of a Roland SPD-30 backup into Battery 4 kits, one cell per sounding pad layer, with the sample linked by relative path.
- Carries over MIDI note (as key range), volume and tuning. Layers without a MIDI note get their own key, 120–127 and then downwards.
- Verifies every kit it writes: container sizes, FastLZ, CRC32, MD5, cells, zones and the sample table. Checks its format model against your own Battery-saved kits first.
- Writes a Markdown report and a per-cell CSV.
- Bonus: copies the same MIDI notes into an SPD-20X backup.
- Standard-library Python only. 62 automated tests: 58 run on synthetic data, 4 need your own Battery templates.

**Known limitations**
- Kits with linked samples have not yet been confirmed to load and play in Battery 4. Parameter-only kits have.
- Needs two kits saved by Battery 4 as templates.
- PCM WAV only. Pan, effects and velocity switching are not carried over.

See the README for the full list.
