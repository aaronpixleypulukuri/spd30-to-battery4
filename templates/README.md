# templates/

The converter rebuilds kits from two kits **saved by your own copy of Battery 4**. They are Native Instruments files, so they are not in this repository, and everything here except this README is ignored by git.

| File | How to make it |
|---|---|
| `Dummy.nbkt` | In Battery 4, start a new empty kit and save it as `Dummy.nbkt`. |
| `wrapper.nbkt` | Load one 16-bit mono WAV into a single cell of an empty kit, change nothing else, and save it as `wrapper.nbkt`. |

Optional: any other kits saved by Battery 4 that you put in this folder are added to the format self-test that runs before each build. More kits make the check stronger.

With both templates in place, `python3 -m unittest discover -s tests` also runs the four checks in `tests/test_with_templates.py`. To keep the templates somewhere else, set `SPD30_TEMPLATES=/path/to/folder` for the tests and pass `--cell-template` and `--wrapper-template` to the converter.
