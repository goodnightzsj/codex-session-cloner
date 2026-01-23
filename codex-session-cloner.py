"""
Safely clone Codex sessions to the currently configured provider.
- Idempotent
- Dry-run supported
- Cleans up legacy unmarked clones
"""

import os
import json
import uuid
import argparse
import sys
from datetime import datetime
from pathlib import Path

# Configuration
SESSIONS_DIR = os.path.expanduser("~/.codex/sessions")
CONFIG_FILE = os.path.expanduser("~/.codex/config.toml")
DEFAULT_PROVIDER = "cliproxyapi"

def get_current_provider():
    """Simple parser to find 'model_provider' in config.toml"""
    if not os.path.exists(CONFIG_FILE):
        return DEFAULT_PROVIDER
        
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line.startswith("model_provider"):
                    parts = line.split("=", 1)
                    if len(parts) == 2:
                        val = parts[1].strip().strip('"').strip("'")
                        return val
    except Exception as e:
        print(f"Error reading config: {e}", file=sys.stderr)
        
    return DEFAULT_PROVIDER

TARGET_PROVIDER = get_current_provider()

def scan_existing_clones(sessions_dir, target_provider):
    """
    Pass 1: Scan ALL files to build an index of already cloned sessions.
    Returns a set of original UUIDs that have already been cloned.
    """
    cloned_from_ids = set()
    total_files = 0
    
    print("Building Clone Index...", end="", flush=True)
    
    for root, dirs, files in os.walk(sessions_dir):
        for file in files:
            if not file.endswith(".jsonl"):
                continue
                
            total_files += 1
            full_path = os.path.join(root, file)
            try:
                with open(full_path, 'r', encoding='utf-8') as f:
                    line = f.readline()
                    if not line: continue
                    meta = json.loads(line)
                    payload = meta.get('payload', {})
                    
                    # If this is a session belonging to our target provider...
                    if payload.get('model_provider') == target_provider:
                        # ...check if it claims to be a clone
                        origin_id = payload.get('cloned_from')
                        if origin_id:
                            cloned_from_ids.add(origin_id)
            except:
                continue
                
    print(f" Done. Found {len(cloned_from_ids)} existing clones out of {total_files} files.")
    return cloned_from_ids

def clone_session(file_path, already_cloned_ids, dry_run=False):
    """
    Reads a session file, clones it if appropriate.
    Returns: (Action, Message)
      Action: 'cloned', 'skipped_exists', 'skipped_provider', 'error', 'skipped_target'
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            
        if not lines:
            return 'error', "Empty file"

        # Parse metadata
        try:
            meta = json.loads(lines[0])
        except json.JSONDecodeError:
            return 'error', "Invalid JSON"

        if meta.get('type') != 'session_meta':
            return 'error', "Not a session file"
            
        payload = meta.get('payload', {})
        current_provider = payload.get('model_provider')
        current_id = payload.get('id')
        
        # 1. Skip if it IS the target provider (we don't clone ourselves)
        if current_provider == TARGET_PROVIDER:
            return 'skipped_target', "Already on target provider"
            
        # 2. Skip if already cloned
        if current_id in already_cloned_ids:
            return 'skipped_exists', f"Already cloned (ID: {current_id})"

        # --- Prepare Clone ---
        
        new_id = str(uuid.uuid4())
        
        # Source Marking (use copy to allow repeated dry-runs safely/pure function)
        new_payload = payload.copy()
        new_payload['id'] = new_id
        new_payload['model_provider'] = TARGET_PROVIDER
        new_payload['cloned_from'] = current_id
        new_payload['original_provider'] = current_provider
        new_payload['clone_timestamp'] = datetime.now().isoformat()
        
        # Update metadata line
        meta['payload'] = new_payload
        lines[0] = json.dumps(meta) + "\n"
        
        # Construct new filename
        file_path_obj = Path(file_path)
        old_filename = file_path_obj.name
        
        # New filename logic
        if current_id and current_id in old_filename:
            new_filename = old_filename.replace(current_id, new_id)
        else:
            if old_filename.endswith(f"{current_id}.jsonl"):
                new_filename = old_filename.replace(f"{current_id}.jsonl", f"{new_id}.jsonl")
            else:
                 new_filename = f"rollout-CLONE-{new_id}.jsonl"

        new_file_path = file_path_obj.parent / new_filename
        
        if new_file_path.exists():
            return 'skipped_exists', "Target file collision"

        if not dry_run:
            with open(new_file_path, 'w', encoding='utf-8') as f_out:
                f_out.writelines(lines)
            return 'cloned', f"Created {new_filename} (from {current_provider})"
        else:
            return 'cloned', f"[DRY-RUN] Would create {new_filename} (from {current_provider})"

    except Exception as e:
        return 'error', str(e)

def scan_for_cleanup(sessions_dir, target_provider, dry_run=False):
    """
    Scans for 'orphan' clones in O(N).
    Single pass to collect all relevant file info, then filter in memory.
    """
    print("Scanning for unmarked clones to clean up...")
    
    # In-memory stores
    # Key: extracted_timestamp_string, Value: List of file_paths
    originals_by_ts = {}
    targets_without_tag_by_ts = {}
    
    files_checked = 0
    
    # 1. Single Pass Scan
    for root, dirs, files in os.walk(sessions_dir):
        for file in files:
            if not file.endswith(".jsonl"): continue
            
            files_checked += 1
            full_path = os.path.join(root, file)
            # Optimization: Try to get timestamp from filename first without opening
            ts = extract_timestamp_from_filename(file)
            if not ts: continue
            
            try:
                with open(full_path, 'r', encoding='utf-8') as f:
                    line = f.readline()
                    if not line: continue
                    meta = json.loads(line)
                    payload = meta.get('payload', {})
                    provider = payload.get('model_provider')
                    
                    if provider == target_provider:
                        # Potential orphan clone?
                        if 'cloned_from' not in payload:
                            if ts not in targets_without_tag_by_ts:
                                targets_without_tag_by_ts[ts] = []
                            targets_without_tag_by_ts[ts].append(full_path)
                    else:
                        # This is an original source
                        originals_by_ts[ts] = True
            except:
                continue

    # 2. Correlate
    files_to_delete = []
    
    for ts, paths in targets_without_tag_by_ts.items():
        # If we have an original with this timestamp...
        if ts in originals_by_ts:
            # ...then these unmarked targets are indeed orphans of that original
            files_to_delete.extend(paths)
                
    # 3. Execute Cleanup
    print(f"Scanned {files_checked} files. Found {len(files_to_delete)} unmarked clones.")
    
    for fpath in files_to_delete:
        if dry_run:
            print(f"[DRY-RUN] Would delete: {fpath}")
        else:
            try:
                os.remove(fpath)
                print(f"[Deleted] {fpath}")
            except Exception as e:
                print(f"[Error] Deleting {fpath}: {e}")

def extract_timestamp_from_filename(filename):
    # rollout-2025-10-10T14-53-44-442631c4... .jsonl
    # We want "2025-10-10T14-53-44"
    try:
        if not filename.startswith("rollout-"): return None
        # Remove prefix
        rest = filename[8:]
        # Remove suffix
        if rest.endswith(".jsonl"): rest = rest[:-6]
        
        parts = filename.replace(".jsonl", "").split('-')
        if len(parts) > 5:
            if (len(parts[-1]) == 12 and len(parts[-2]) == 4 and 
                len(parts[-3]) == 4 and len(parts[-4]) == 4 and len(parts[-5]) == 8):
                return "-".join(parts[:-5])
        return None
    except:
        return None

def main():
    parser = argparse.ArgumentParser(description="Clone Codex sessions to current provider.")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing files")
    parser.add_argument("--clean", action="store_true", help="Remove unmarked clones from previous runs")
    args = parser.parse_args()

    print(f"Target Provider: {TARGET_PROVIDER}")
    print(f"Sessions Dir:    {SESSIONS_DIR}")
    if args.dry_run:
        print("!! DRY RUN MODE ENABLED !!")
    print("-" * 40)

    if args.clean:
        scan_for_cleanup(SESSIONS_DIR, TARGET_PROVIDER, dry_run=args.dry_run)
        print("\nCleanup scan complete. Re-run without --clean to clone.")
        return

    # Pass 1: Index
    already_cloned = scan_existing_clones(SESSIONS_DIR, TARGET_PROVIDER)
    
    # Pass 2: Clone
    stats = {
        'cloned': 0,
        'skipped_exists': 0,
        'skipped_target': 0,
        'error': 0
        # 'skipped_provider' removed as it was unused
    }
    
    print("\nScanning candidates...")
    
    for root, dirs, files in os.walk(SESSIONS_DIR):
        for file in files:
            if file.endswith(".jsonl"):
                full_path = os.path.join(root, file)
                action, msg = clone_session(full_path, already_cloned, dry_run=args.dry_run)
                
                stats[action] = stats.get(action, 0) + 1
                
                if action == 'cloned':
                    print(f"[+] {msg}")
                elif action == 'error':
                    print(f"[!] Error in {file}: {msg}")
                    
    print("\n" + "="*30)
    print("Summary:")
    print(f"  Target Provider: {TARGET_PROVIDER}")
    print(f"  Cloned (New):    {stats['cloned']}")
    print(f"  Skipped (Target):{stats['skipped_target']} (Files already belonging to {TARGET_PROVIDER})")
    print(f"  Skipped (Done):  {stats['skipped_exists']} (Others already cloned previously)")
    print(f"  Errors:          {stats['error']}")
    print("="*30)
    
    if args.dry_run:
        print("\nThis was a DRY RUN. No files were created.")

if __name__ == "__main__":
    main()
