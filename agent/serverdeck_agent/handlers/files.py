import os
import shutil
import logging
import base64
import tempfile
from datetime import datetime

logger = logging.getLogger("serverdeck.agent.handlers.files")

async def handle_list(params: dict) -> dict:
    """List contents of a directory."""
    path = params.get("path", "/")
    if not os.path.isabs(path):
        return {"error": "Path must be absolute"}
    
    if not os.path.exists(path):
        return {"error": "Path does not exist"}
    
    if not os.path.isdir(path):
        return {"error": "Path is not a directory"}
    
    try:
        items = []
        for entry in os.scandir(path):
            try:
                info = entry.stat()
                items.append({
                    "name": entry.name,
                    "is_dir": entry.is_dir(),
                    "size": info.st_size,
                    "mtime": datetime.fromtimestamp(info.st_mtime).isoformat(),
                    "permissions": oct(info.st_mode)[-3:]
                })
            except Exception:
                # Skip entries we can't access
                continue
                
        # Sort: directories first, then alphabetical
        items.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
        
        return {
            "path": path,
            "items": items,
            "parent": os.path.dirname(path) if path != "/" else None
        }
    except Exception as e:
        return {"error": str(e)}

async def handle_read(params: dict) -> dict:
    """Read file content."""
    path = params.get("path")
    if not path:
        return {"error": "Path is required"}
    
    if not os.path.isfile(path):
        return {"error": "Path is not a file"}
        
    try:
        # Check file size (don't read massive files into memory)
        if os.path.getsize(path) > 10 * 1024 * 1024: # 10MB limit
            return {"error": "File too large (max 10MB)"}
            
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
            
        return {"content": content, "path": path}
    except Exception as e:
        return {"error": str(e)}

async def handle_write(params: dict) -> dict:
    """Write content to a file."""
    path = params.get("path")
    content = params.get("content", "")
    if not path:
        return {"error": "Path is required"}
        
    try:
        # Create directory if it doesn't exist
        os.makedirs(os.path.dirname(path), exist_ok=True)
        
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
            
        return {"status": "success", "path": path}
    except Exception as e:
        return {"error": str(e)}

# Critical paths that must never be deleted, even by an authorized operator.
_PROTECTED_PATHS = {
    "/", "/bin", "/boot", "/dev", "/etc", "/home", "/lib", "/lib64",
    "/proc", "/root", "/run", "/sbin", "/srv", "/sys", "/usr", "/var",
}


async def handle_delete(params: dict) -> dict:
    """Delete a file or directory."""
    path = params.get("path")
    if not path:
        return {"error": "Path is required"}

    if not os.path.isabs(path):
        return {"error": "Path must be absolute"}

    # Normalize (resolve "..", trailing slashes, symlinks) before the guard so
    # paths like "/root/" or "/etc/../etc" cannot slip past the protected set.
    normalized = os.path.realpath(path)
    if normalized in _PROTECTED_PATHS:
        return {"error": "Safety first: cannot delete a protected system directory"}

    try:
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
        return {"status": "success"}
    except Exception as e:
        return {"error": str(e)}

async def handle_mkdir(params: dict) -> dict:
    """Create a directory."""
    path = params.get("path")
    if not path:
        return {"error": "Path is required"}
        
    try:
        os.makedirs(path, exist_ok=True)
        return {"status": "success"}
    except Exception as e:
        return {"error": str(e)}

async def handle_download(params: dict) -> dict:
    """
    Prepare a file or folder for download.
    If it's a folder, it zips it first.
    Returns base64 encoded data.
    """
    path = params.get("path")
    if not path or not os.path.exists(path):
        return {"error": "Valid path is required"}

    temp_zip = None
    try:
        target_path = path
        filename = os.path.basename(path)
        
        # If it's a directory, zip it
        if os.path.isdir(path):
            # mkstemp creates the file securely (no symlink/predictable-name race);
            # we only need a unique base path for make_archive.
            fd, temp_zip = tempfile.mkstemp(suffix=".zip")
            os.close(fd)
            base_dir = os.path.dirname(path)
            root_dir = os.path.basename(path)
            shutil.make_archive(temp_zip.replace(".zip", ""), "zip", base_dir, root_dir)
            target_path = temp_zip
            filename += ".zip"

        # Check size before base64 encoding (encoding increases size by ~33%)
        file_size = os.path.getsize(target_path)
        if file_size > 50 * 1024 * 1024: # 50MB limit for this method
            return {"error": "File too large for direct download (max 50MB)"}

        with open(target_path, "rb") as f:
            content = base64.b64encode(f.read()).decode("utf-8")

        return {
            "filename": filename,
            "content": content,
            "mime": "application/zip" if os.path.isdir(path) else "application/octet-stream"
        }
    except Exception as e:
        return {"error": str(e)}
    finally:
        if temp_zip and os.path.exists(temp_zip):
            try: os.remove(temp_zip)
            except: pass

async def handle_upload(params: dict) -> dict:
    """
    Upload a file (base64 encoded).
    """
    path = params.get("path")
    filename = params.get("filename")
    content_b64 = params.get("content") # Base64 string

    if not path or not filename or content_b64 is None:
        return {"error": "Path, filename, and content are required"}

    try:
        full_path = os.path.join(path, filename)
        os.makedirs(path, exist_ok=True)
        
        content = base64.b64decode(content_b64)
        
        with open(full_path, "wb") as f:
            f.write(content)
            
        return {"status": "success", "path": full_path}
    except Exception as e:
        return {"error": str(e)}
