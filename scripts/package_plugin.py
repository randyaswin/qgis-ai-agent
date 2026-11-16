#!/usr/bin/env python3
"""Build and package a clean, production-ready QGIS plugin ZIP archive.

Validates metadata.txt against QGIS Plugin Repository standards, excludes build artifacts,
and packages the plugin into:
  - dist/qgis_ai_agent.zip (ready for plugins.qgis.org upload)
  - dist/qgis_ai_agent-<version>.zip (versioned release asset)
"""
import configparser
import hashlib
import os
import shutil
import sys
import zipfile

PLUGIN_NAME = "qgis_ai_agent"
REQUIRED_METADATA_FIELDS = [
    "name",
    "qgisMinimumVersion",
    "description",
    "version",
    "author",
    "email",
]

EXCLUDED_DIRS = {
    "__pycache__",
    ".pytest_cache",
    ".git",
    ".github",
    ".idea",
    ".vscode",
}

EXCLUDED_EXTENSIONS = {
    ".pyc",
    ".pyo",
    ".pyd",
    ".swp",
    ".swo",
    ".bak",
    ".tmp",
    ".DS_Store",
}

EXCLUDED_FILES = {
    ".DS_Store",
    "Thumbs.db",
    ".gitignore",
    ".gitattributes",
}


def compute_sha256(filepath):
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def validate_metadata(plugin_dir):
    metadata_path = os.path.join(plugin_dir, "metadata.txt")
    if not os.path.exists(metadata_path):
        sys.exit(f"❌ Error: metadata.txt not found at {metadata_path}")

    config = configparser.ConfigParser()
    try:
        config.read(metadata_path, encoding="utf-8")
    except Exception as e:
        sys.exit(f"❌ Error reading metadata.txt: {e}")

    if "general" not in config:
        sys.exit("❌ Error: metadata.txt is missing [general] section.")

    general = config["general"]
    missing = [f for f in REQUIRED_METADATA_FIELDS if f not in general or not general[f].strip()]
    if missing:
        sys.exit(f"❌ Error: metadata.txt is missing required fields: {', '.join(missing)}")

    version = general["version"].strip()
    name = general["name"].strip()
    min_ver = general["qgisMinimumVersion"].strip()
    author = general["author"].strip()
    email = general["email"].strip()

    print(f"✅ Metadata validated successfully:")
    print(f"   • Name:         {name}")
    print(f"   • Version:      {version}")
    print(f"   • QGIS Min Ver: {min_ver}")
    print(f"   • Author:       {author} <{email}>")
    return version


def should_include(rel_path):
    parts = rel_path.replace("\\", "/").split("/")
    for p in parts:
        if p in EXCLUDED_DIRS:
            return False
    filename = parts[-1]
    if filename in EXCLUDED_FILES:
        return False
    _, ext = os.path.splitext(filename)
    if ext in EXCLUDED_EXTENSIONS:
        return False
    return True


def package_plugin(repo_root):
    plugin_dir = os.path.join(repo_root, PLUGIN_NAME)
    if not os.path.isdir(plugin_dir):
        sys.exit(f"❌ Error: Plugin directory '{plugin_dir}' not found.")

    version = validate_metadata(plugin_dir)

    dist_dir = os.path.join(repo_root, "dist")
    os.makedirs(dist_dir, exist_ok=True)

    zip_filename_canonical = os.path.join(dist_dir, f"{PLUGIN_NAME}.zip")
    zip_filename_versioned = os.path.join(dist_dir, f"{PLUGIN_NAME}-{version}.zip")

    # Remove stale builds if present
    for z in (zip_filename_canonical, zip_filename_versioned):
        if os.path.exists(z):
            os.remove(z)

    print(f"\n📦 Packaging files into {zip_filename_canonical}...")
    file_count = 0
    total_uncompressed_bytes = 0

    with zipfile.ZipFile(zip_filename_canonical, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zipf:
        for root, dirs, files in os.walk(plugin_dir):
            # Prune excluded directories in-place so os.walk doesn't recurse into them
            dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]

            for file in files:
                abs_path = os.path.join(root, file)
                rel_path = os.path.relpath(abs_path, repo_root)

                if not should_include(rel_path):
                    continue

                # QGIS requires the top-level folder inside the ZIP to be the plugin folder name
                arcname = rel_path.replace("\\", "/")
                zipf.write(abs_path, arcname)
                file_count += 1
                total_uncompressed_bytes += os.path.getsize(abs_path)

        # QGIS Plugin Repository requires LICENSE inside the plugin root directory
        plugin_license_arcname = f"{PLUGIN_NAME}/LICENSE"
        if not os.path.exists(os.path.join(plugin_dir, "LICENSE")):
            repo_license = os.path.join(repo_root, "LICENSE")
            if os.path.exists(repo_license):
                zipf.write(repo_license, plugin_license_arcname)
                file_count += 1
                total_uncompressed_bytes += os.path.getsize(repo_license)

    # Make versioned duplicate
    shutil.copy2(zip_filename_canonical, zip_filename_versioned)

    # Verify created zip archive structure
    print(f"\n🔍 Verifying ZIP package structure...")
    with zipfile.ZipFile(zip_filename_canonical, "r") as zipf:
        namelist = zipf.namelist()
        expected_meta = f"{PLUGIN_NAME}/metadata.txt"
        expected_init = f"{PLUGIN_NAME}/__init__.py"
        expected_license = f"{PLUGIN_NAME}/LICENSE"
        if expected_meta not in namelist:
            sys.exit(f"❌ Verification failed: '{expected_meta}' is missing from ZIP root!")
        if expected_init not in namelist:
            sys.exit(f"❌ Verification failed: '{expected_init}' is missing from ZIP root!")
        if expected_license not in namelist:
            sys.exit(f"❌ Verification failed: '{expected_license}' is missing from ZIP root!")

        # Verify no pycache or forbidden extensions leaked into zip
        for name in namelist:
            if "__pycache__" in name or name.endswith(".pyc"):
                sys.exit(f"❌ Verification failed: Forbidden file found inside ZIP: '{name}'")

    zip_size_bytes = os.path.getsize(zip_filename_canonical)
    zip_size_mb = zip_size_bytes / (1024 * 1024)
    sha256_canonical = compute_sha256(zip_filename_canonical)

    # Write checksums file
    checksum_file = os.path.join(dist_dir, "checksums.sha256")
    with open(checksum_file, "w") as f:
        f.write(f"{sha256_canonical}  {PLUGIN_NAME}.zip\n")
        f.write(f"{sha256_canonical}  {PLUGIN_NAME}-{version}.zip\n")

    print(f"\n🎉 Package created successfully!")
    print(f"   • Total packaged files: {file_count}")
    print(f"   • Uncompressed size:    {total_uncompressed_bytes / (1024*1024):.2f} MB")
    print(f"   • ZIP Archive Size:     {zip_size_mb:.2f} MB ({zip_size_bytes:,} bytes)")
    print(f"   • SHA256 Checksum:      {sha256_canonical}")
    print(f"\n📁 Output artifacts in 'dist/':")
    print(f"   1. {os.path.basename(zip_filename_canonical)} (Ready to upload to plugins.qgis.org)")
    print(f"   2. {os.path.basename(zip_filename_versioned)} (Release asset with version tag)")
    print(f"   3. {os.path.basename(checksum_file)}")

    # Set GitHub Actions output parameters if running in CI
    gh_output = os.environ.get("GITHUB_OUTPUT")
    if gh_output:
        with open(gh_output, "a") as f:
            f.write(f"version={version}\n")
            f.write(f"zip_canonical={zip_filename_canonical}\n")
            f.write(f"zip_versioned={zip_filename_versioned}\n")
            f.write(f"checksum={sha256_canonical}\n")

    return version


if __name__ == "__main__":
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    package_plugin(repo_root)

