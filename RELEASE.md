# cicwave Release Process

This document describes how to release new versions of cicwave to PyPI.

## Overview

cicwave uses automated releases via GitHub Actions. The workflow is triggered when you push a version tag or manually via the GitHub web interface.

## Release Methods

### Method 1: Automated Release via Git Tags (Recommended)

1. **Update the version** in `pyproject.toml`:
   ```toml
   [project]
   name = "cicwave"
   version = "0.6.0"  # Update this
   ```

2. **Commit and tag the release**:
   ```bash
   git add pyproject.toml
   git commit -m "Bump version to 0.6.0"
   git tag v0.6.0
   git push origin main
   git push origin v0.6.0
   ```

3. **Monitor the release**: The GitHub Action will automatically:
   - Build the package
   - Run tests (if available)
   - Upload to PyPI
   - Create a GitHub release

### Method 2: Manual Release via Make

For local testing or manual control:

```bash
# Check current version
make version

# Run tests and build
make test
make build

# Upload to Test PyPI first (optional)
make test_upload

# Upload to production PyPI
make upload
```

### Method 3: Manual Trigger via GitHub

1. Go to the [Actions tab](../../actions/workflows/release.yml) in GitHub
2. Click "Run workflow"
3. Select the branch and click "Run workflow"

## Prerequisites

### PyPI API Token

You need a PyPI API token configured as a GitHub secret:

1. Create a PyPI account at https://pypi.org
2. Generate an API token in your PyPI account settings
3. Add the token as `PYPI_API_TOKEN` in your GitHub repository secrets:
   - Go to Settings → Secrets and variables → Actions
   - Click "New repository secret"
   - Name: `PYPI_API_TOKEN`
   - Value: Your PyPI token (starts with `pypi-`)

### Local Development Setup

For manual releases, install the required tools:

```bash
pip install build twine
```

## Versioning Strategy

cicwave follows [Semantic Versioning](https://semver.org/):

- **Major** (X.0.0): Breaking changes to the API
- **Minor** (0.X.0): New features, backwards compatible
- **Patch** (0.0.X): Bug fixes, backwards compatible

### Version Examples

- `0.5.0` → `0.5.1`: Bug fix
- `0.5.1` → `0.6.0`: New feature
- `0.6.0` → `1.0.0`: Breaking change

## Available Make Targets

| Target | Description |
|--------|-------------|
| `make version` | Show current version from pyproject.toml |
| `make test` | Run unit tests |
| `make lint` | Run code linting (if ruff installed) |
| `make format` | Format code (if ruff installed) |
| `make build` | Build wheel and source distribution |
| `make clean` | Clean build artifacts |
| `make check` | Test that package can be imported |
| `make test_upload` | Upload to Test PyPI |
| `make upload` | Upload to production PyPI |
| `make release` | Full release process with confirmation |

## Troubleshooting

### Version Already Exists

If you see an error that the version already exists on PyPI:
1. Update the version number in `pyproject.toml`
2. Create a new tag with the updated version
3. Push the new tag

### Build Failures

Common issues:
- **Missing dependencies**: Ensure all dependencies in `pyproject.toml` are correct
- **Import errors**: Test locally with `make check` before releasing
- **Test failures**: Fix any failing tests before tagging a release

### Manual Release Steps

If automation fails, you can release manually:

```bash
# Clean previous builds
make clean

# Build the package
make build

# Check the built package
twine check dist/*

# Upload to PyPI
twine upload dist/*
```

## Post-Release Checklist

After a successful release:

1. ✅ Verify the package appears on [PyPI](https://pypi.org/project/cicwave/)
2. ✅ Test installation: `pip install cicwave==X.Y.Z`
3. ✅ Check that the GitHub release was created
4. ✅ Update documentation if needed
5. ✅ Consider updating dependent projects that use cicwave

## Files Involved in Releases

- `pyproject.toml` - Package metadata and version
- `.github/workflows/release.yml` - Automated release workflow  
- `Makefile` - Local release commands
- `RELEASE.md` - This documentation (you are here)