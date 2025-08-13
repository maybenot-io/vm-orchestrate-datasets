#!/bin/bash

# Read all paths from config.json
CONFIG_FILE="env/config.json"
[ -f "$CONFIG_FILE" ] || { echo "Error: Config file not found: $CONFIG_FILE"; exit 1; }

# Set collection log path
COLLECTION_LOG="collectionlog.txt"

# Check for required dependencies
command -v jq >/dev/null 2>&1 || { echo "Error: jq is required but not installed"; exit 1; }
command -v pv >/dev/null 2>&1 || { echo "Error: pv is required but not installed"; exit 1; }

# Extract all paths and settings from config
DATADIR=$(jq -r '.server.datadir' "$CONFIG_FILE")
URLLIST=$(jq -r '.server.urllist' "$CONFIG_FILE")
VPNLIST=$(jq -r '.server.vpnlist' "$CONFIG_FILE")
SAMPLE_SIZE=$(jq -r '.server.samples' "$CONFIG_FILE")

# Validate extracted values
[ -z "$DATADIR" ] || [ "$DATADIR" = "null" ] && { echo "Error: Could not read datadir from $CONFIG_FILE"; exit 1; }
[ -z "$URLLIST" ] || [ "$URLLIST" = "null" ] && { echo "Error: Could not read urllist from $CONFIG_FILE"; exit 1; }
[ -z "$VPNLIST" ] || [ "$VPNLIST" = "null" ] && { echo "Error: Could not read vpnlist from $CONFIG_FILE"; exit 1; }
[ -z "$SAMPLE_SIZE" ] || [ "$SAMPLE_SIZE" = "null" ] && { echo "Error: Could not read samples from $CONFIG_FILE"; exit 1; }

# Validate that files/directories exist
[ -d "$DATADIR" ] || { echo "Error: Data directory not found: $DATADIR"; exit 1; }
[ -f "$URLLIST" ] || { echo "Error: URL list file not found: $URLLIST"; exit 1; }
[ -f "$VPNLIST" ] || { echo "Error: VPN list file not found: $VPNLIST"; exit 1; }
[ -f "$COLLECTION_LOG" ] || { echo "Error: Collection log not found: $COLLECTION_LOG"; exit 1; }

# Convert to absolute paths
CONFIG_FILE=$(realpath "$CONFIG_FILE")
URLLIST=$(realpath "$URLLIST")
VPNLIST=$(realpath "$VPNLIST")
DATADIR=$(realpath "$DATADIR")
COLLECTION_LOG=$(realpath "$COLLECTION_LOG")

# Set archive parameters
BASE_NAME=$(basename "$URLLIST" .txt)
ARCHIVE_NAME="${BASE_NAME}_${SAMPLE_SIZE}_samples.tar.gz"
TAR_ROOT="${BASE_NAME}_${SAMPLE_SIZE}"

echo "Creating archive $ARCHIVE_NAME with:"
echo "  URL list: $URLLIST"
echo "  VPN list: $VPNLIST"
echo "  Data directory: $DATADIR"
echo "  Samples: $SAMPLE_SIZE"
echo "  Collection log: $COLLECTION_LOG"

# Calculate total size for progress monitoring
echo -e "\nCalculating total size..."
TOTAL_SIZE=$(du -sb "$DATADIR" "$CONFIG_FILE" "$URLLIST" "$VPNLIST" "$COLLECTION_LOG" | awk '{sum += $1} END {print sum}')
echo "Total data to compress: $(numfmt --to=iec $TOTAL_SIZE)"

# Create archive with pv progress monitoring
echo -e "\nCompressing data (this may take a while)..."
tar -cf - \
  --transform "s|^|$TAR_ROOT/|" \
  -C "$(dirname "$CONFIG_FILE")" "$(basename "$CONFIG_FILE")" \
  -C "$(dirname "$URLLIST")" "$(basename "$URLLIST")" \
  -C "$(dirname "$VPNLIST")" "$(basename "$VPNLIST")" \
  -C "$(dirname "$COLLECTION_LOG")" "$(basename "$COLLECTION_LOG")" \
  -C "$(dirname "$DATADIR")" "$(basename "$DATADIR")" \
  | pv -s "$TOTAL_SIZE" -N "Compression Progress" | gzip > "$ARCHIVE_NAME"

# Verify archive integrity
echo -e "\nVerifying archive..."
if tar -tzf "$ARCHIVE_NAME" >/dev/null; then
    echo -e "\n✅ Archive created successfully!"
    echo "File: $(realpath "$ARCHIVE_NAME")"
    echo "Size: $(du -h "$ARCHIVE_NAME" | cut -f1)"

    # Show archive structure
    echo -e "\nArchive structure:"
    echo "${TAR_ROOT}/"
    echo "├── $(basename "$CONFIG_FILE")"
    echo "├── $(basename "$URLLIST")"
    echo "├── $(basename "$VPNLIST")"
    echo "├── $(basename "$COLLECTION_LOG")"
    echo "└── $(basename "$DATADIR")/"
    echo "    └── [data files]"

    # Show first few files as example
    echo -e "\nFirst few files in archive:"
    tar -tzf "$ARCHIVE_NAME" | head -n 10
    echo "..."
else
    echo "❌ Error: Archive verification failed!"
    exit 1
fi