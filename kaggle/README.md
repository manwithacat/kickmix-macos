# Kaggle mirrors

`.github/workflows/kaggle-publish.yml` copies each GitHub release to Kaggle so internet-off
notebooks get the same build:

- `wheels-dataset/`: metadata for the dataset `manwithacat/kickmix-fork-wheels`. Each release
  pushes a new dataset version holding the Linux x86-64 wheels (Kaggle's CPU image) and a
  `SHA256SUMS` listing exactly those wheels, taken unchanged from the release.
- `kickmix-tools/`: metadata for the utility script `manwithacat/kickmix-tools`. Each release
  pushes `tools/kickmix_tools.py` from the tagged commit as a new script version.
- `quickstart/`: `build.py` generates `notebooks/kaggle-quickstart.ipynb` (run it after editing;
  `--check` fails if the committed notebook is stale), and `kernel-metadata.json` describes the public
  notebook `manwithacat/kickmix-quickstart` (competition data, the fork and engine wheels datasets, the
  public circuits dataset, the `kickmix-tools` utility script; internet off).
  `.github/workflows/kaggle-quickstart.yml` pushes it after each release, once the new `kickmix_tools` has
  finished its run on Kaggle, and can be run by hand (`dry_run` checks and authenticates only).
  The engine wheels dataset `manwithacat/kickmix-engine-wheels` is maintained from the kickmix-engine
  releases, not by this repository.

GitHub releases are the source of truth; Kaggle only mirrors them. Authentication uses the
repository secret `KAGGLE_API_TOKEN` (a Kaggle API token). The dataset's About text, tags and
other metadata are maintained on Kaggle, not here: a version push changes files only.
