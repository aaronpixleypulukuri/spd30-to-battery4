# SPD-30 to Battery 4 build report

| Check | Result |
|---|---|
| Kits written | 2 (2 verified, 0 failed) |
| Cells written | 10 (10 pass read-back) |
| Cells with a linked sample | 9 |
| Battery-saved reference kits re-serialise byte for byte, CRC and MD5 valid | wrapper.nbkt: yes |
| Unedited PCM WAV zones of those kits reproduced exactly by the zone builder | 0 of 0 |
| Cell template's cells identical to the wrapper kit's empty cells | yes |
| Loaded and played in Battery 4 | **not tested** |

Volume and tune are written to the cell (engine gain, quick-access level and quick-access tune); the zone stays at 1.0.

## Layers in the backup

| | Count |
|---|---|
| Layers (100 kits x 12 pads x 2) | 2400 |
| Active | 10 |
| Inactive: instrument OFF | 1191 |
| Inactive: layer B disabled (single mode) | 1198 |
| Inactive: zero volume | 1 |
| Active layers with MIDI note OFF (keys 120-127 assigned in cell order, then 119 downwards) | 2 |

## Problems

- kit 001 PAD 5 A: active layer has no WAV (cell written without a sample)
- kit 001: Layer B/1-KIK 002.wav matches no active layer (not linked)

## Kits

| Kit | Folder | Cells | Samples linked | Result |
|---|---|---|---|---|
| 001 | 001 Demo Rock | 7 | 6 | verified |
| 002 | 002 Demo Percussion | 3 | 3 | verified |
| 003 | Demo Empty | 0 | 0 | no active layer - not written |
| 004 | Kit 4 | 0 | 0 | no active layer - not written |
| 005 | Kit 5 | 0 | 0 | no active layer - not written |
| 006 | Kit 6 | 0 | 0 | no active layer - not written |
| 007 | Kit 7 | 0 | 0 | no active layer - not written |
| 008 | Kit 8 | 0 | 0 | no active layer - not written |
| 009 | Kit 9 | 0 | 0 | no active layer - not written |
| 010 | Kit 10 | 0 | 0 | no active layer - not written |
| 011 | Kit 11 | 0 | 0 | no active layer - not written |
| 012 | Kit 12 | 0 | 0 | no active layer - not written |
| 013 | Kit 13 | 0 | 0 | no active layer - not written |
| 014 | Kit 14 | 0 | 0 | no active layer - not written |
| 015 | Kit 15 | 0 | 0 | no active layer - not written |
| 016 | Kit 16 | 0 | 0 | no active layer - not written |
| 017 | Kit 17 | 0 | 0 | no active layer - not written |
| 018 | Kit 18 | 0 | 0 | no active layer - not written |
| 019 | Kit 19 | 0 | 0 | no active layer - not written |
| 020 | Kit 20 | 0 | 0 | no active layer - not written |
| 021 | Kit 21 | 0 | 0 | no active layer - not written |
| 022 | Kit 22 | 0 | 0 | no active layer - not written |
| 023 | Kit 23 | 0 | 0 | no active layer - not written |
| 024 | Kit 24 | 0 | 0 | no active layer - not written |
| 025 | Kit 25 | 0 | 0 | no active layer - not written |
| 026 | Kit 26 | 0 | 0 | no active layer - not written |
| 027 | Kit 27 | 0 | 0 | no active layer - not written |
| 028 | Kit 28 | 0 | 0 | no active layer - not written |
| 029 | Kit 29 | 0 | 0 | no active layer - not written |
| 030 | Kit 30 | 0 | 0 | no active layer - not written |
| 031 | Kit 31 | 0 | 0 | no active layer - not written |
| 032 | Kit 32 | 0 | 0 | no active layer - not written |
| 033 | Kit 33 | 0 | 0 | no active layer - not written |
| 034 | Kit 34 | 0 | 0 | no active layer - not written |
| 035 | Kit 35 | 0 | 0 | no active layer - not written |
| 036 | Kit 36 | 0 | 0 | no active layer - not written |
| 037 | Kit 37 | 0 | 0 | no active layer - not written |
| 038 | Kit 38 | 0 | 0 | no active layer - not written |
| 039 | Kit 39 | 0 | 0 | no active layer - not written |
| 040 | Kit 40 | 0 | 0 | no active layer - not written |
| 041 | Kit 41 | 0 | 0 | no active layer - not written |
| 042 | Kit 42 | 0 | 0 | no active layer - not written |
| 043 | Kit 43 | 0 | 0 | no active layer - not written |
| 044 | Kit 44 | 0 | 0 | no active layer - not written |
| 045 | Kit 45 | 0 | 0 | no active layer - not written |
| 046 | Kit 46 | 0 | 0 | no active layer - not written |
| 047 | Kit 47 | 0 | 0 | no active layer - not written |
| 048 | Kit 48 | 0 | 0 | no active layer - not written |
| 049 | Kit 49 | 0 | 0 | no active layer - not written |
| 050 | Kit 50 | 0 | 0 | no active layer - not written |
| 051 | Kit 51 | 0 | 0 | no active layer - not written |
| 052 | Kit 52 | 0 | 0 | no active layer - not written |
| 053 | Kit 53 | 0 | 0 | no active layer - not written |
| 054 | Kit 54 | 0 | 0 | no active layer - not written |
| 055 | Kit 55 | 0 | 0 | no active layer - not written |
| 056 | Kit 56 | 0 | 0 | no active layer - not written |
| 057 | Kit 57 | 0 | 0 | no active layer - not written |
| 058 | Kit 58 | 0 | 0 | no active layer - not written |
| 059 | Kit 59 | 0 | 0 | no active layer - not written |
| 060 | Kit 60 | 0 | 0 | no active layer - not written |
| 061 | Kit 61 | 0 | 0 | no active layer - not written |
| 062 | Kit 62 | 0 | 0 | no active layer - not written |
| 063 | Kit 63 | 0 | 0 | no active layer - not written |
| 064 | Kit 64 | 0 | 0 | no active layer - not written |
| 065 | Kit 65 | 0 | 0 | no active layer - not written |
| 066 | Kit 66 | 0 | 0 | no active layer - not written |
| 067 | Kit 67 | 0 | 0 | no active layer - not written |
| 068 | Kit 68 | 0 | 0 | no active layer - not written |
| 069 | Kit 69 | 0 | 0 | no active layer - not written |
| 070 | Kit 70 | 0 | 0 | no active layer - not written |
| 071 | Kit 71 | 0 | 0 | no active layer - not written |
| 072 | Kit 72 | 0 | 0 | no active layer - not written |
| 073 | Kit 73 | 0 | 0 | no active layer - not written |
| 074 | Kit 74 | 0 | 0 | no active layer - not written |
| 075 | Kit 75 | 0 | 0 | no active layer - not written |
| 076 | Kit 76 | 0 | 0 | no active layer - not written |
| 077 | Kit 77 | 0 | 0 | no active layer - not written |
| 078 | Kit 78 | 0 | 0 | no active layer - not written |
| 079 | Kit 79 | 0 | 0 | no active layer - not written |
| 080 | Kit 80 | 0 | 0 | no active layer - not written |
| 081 | Kit 81 | 0 | 0 | no active layer - not written |
| 082 | Kit 82 | 0 | 0 | no active layer - not written |
| 083 | Kit 83 | 0 | 0 | no active layer - not written |
| 084 | Kit 84 | 0 | 0 | no active layer - not written |
| 085 | Kit 85 | 0 | 0 | no active layer - not written |
| 086 | Kit 86 | 0 | 0 | no active layer - not written |
| 087 | Kit 87 | 0 | 0 | no active layer - not written |
| 088 | Kit 88 | 0 | 0 | no active layer - not written |
| 089 | Kit 89 | 0 | 0 | no active layer - not written |
| 090 | Kit 90 | 0 | 0 | no active layer - not written |
| 091 | Kit 91 | 0 | 0 | no active layer - not written |
| 092 | Kit 92 | 0 | 0 | no active layer - not written |
| 093 | Kit 93 | 0 | 0 | no active layer - not written |
| 094 | Kit 94 | 0 | 0 | no active layer - not written |
| 095 | Kit 95 | 0 | 0 | no active layer - not written |
| 096 | Kit 96 | 0 | 0 | no active layer - not written |
| 097 | Kit 97 | 0 | 0 | no active layer - not written |
| 098 | Kit 98 | 0 | 0 | no active layer - not written |
| 099 | Kit 99 | 0 | 0 | no active layer - not written |
| 100 | Kit 100 | 0 | 0 | no active layer - not written |
