from typing import Dict, Any, List, Optional
from fastapi import HTTPException
from app.schemas.competition import FieldType


def normalize_submission_settings(data: Dict[str, Any], is_update: bool = False) -> Dict[str, Any]:
    """
    Normalizes and synchronizes competition submission configuration fields:
      - allow_file_upload (bool): Allow documents, images, zip files
      - allow_video_link (bool): Allow YouTube video links
      - allow_audio_upload (bool): Allow MP3 audio uploads (.mp3)
      - submission_formats (List[str]): List of enabled format names (e.g. ['FILE', 'VIDEO_LINK', 'AUDIO'])
      - submission_format_config (dict): Full configuration metadata
      - dynamic_submission_fields (List[dict]): Schema of fields exposed to participants

    Whichever fields the admin enables during creation or update, only those specific
    fields are exposed and accepted for submissions.
    """
    # Check if any submission format toggle was provided in data
    raw_file = data.get("allow_file_upload")
    raw_video = data.get("allow_video_link")
    raw_audio = data.get("allow_audio_upload")
    raw_formats = data.get("submission_formats")
    raw_cfg = data.get("submission_format_config")
    if hasattr(raw_cfg, "model_dump"):
        raw_cfg = raw_cfg.model_dump()
    raw_dyn = data.get("dynamic_submission_fields")

    has_explicit_flags = any(v is not None for v in [raw_file, raw_video, raw_audio])
    has_formats = bool(raw_formats and isinstance(raw_formats, list))
    has_cfg = bool(raw_cfg and isinstance(raw_cfg, dict))
    has_dyn = bool(raw_dyn and isinstance(raw_dyn, list) and len(raw_dyn) > 0)

    # If this is an update and no submission-related fields were provided, leave as-is
    if is_update and not (has_explicit_flags or has_formats or has_cfg or has_dyn):
        return data

    f_enabled = False
    v_enabled = False
    a_enabled = False

    if has_explicit_flags:
        if raw_file is not None:
            f_enabled = bool(raw_file)
        if raw_video is not None:
            v_enabled = bool(raw_video)
        if raw_audio is not None:
            a_enabled = bool(raw_audio)

    elif has_formats:
        fmt_upper = [str(x).strip().upper() for x in raw_formats if x]
        if any(x in fmt_upper for x in ["FILE", "FILES", "DOCUMENT", "IMAGE"]):
            f_enabled = True
        if any(x in fmt_upper for x in ["VIDEO", "VIDEO_LINK", "YOUTUBE", "VIDEO_URL"]):
            v_enabled = True
        if any(x in fmt_upper for x in ["AUDIO", "AUDIO_UPLOAD", "MP3", "AUDIO_FILE"]):
            a_enabled = True

    elif has_cfg and isinstance(raw_cfg, dict):
        if raw_cfg.get("allow_file_upload") is not None:
            f_enabled = bool(raw_cfg["allow_file_upload"])
        if raw_cfg.get("allow_video_link") is not None:
            v_enabled = bool(raw_cfg["allow_video_link"])
        if raw_cfg.get("allow_audio_upload") is not None:
            a_enabled = bool(raw_cfg["allow_audio_upload"])
        if raw_cfg.get("allowed_submission_types"):
            t_upper = [str(x).strip().upper() for x in raw_cfg["allowed_submission_types"] if x]
            if any(x in t_upper for x in ["FILE", "DOCUMENT", "IMAGE"]):
                f_enabled = True
            if any(x in t_upper for x in ["VIDEO", "VIDEO_LINK", "YOUTUBE"]):
                v_enabled = True
            if any(x in t_upper for x in ["AUDIO", "MP3"]):
                a_enabled = True

    elif has_dyn:
        # Infer settings from provided dynamic_submission_fields
        for field in raw_dyn:
            f_dict = field.model_dump() if hasattr(field, "model_dump") else (field if isinstance(field, dict) else {})
            f_type = str(f_dict.get("type", "")).upper()
            f_name = str(f_dict.get("name", "")).lower()
            f_label = str(f_dict.get("label", "")).lower()

            if "video" in f_name or "video" in f_label or f_type == "VIDEO":
                v_enabled = True
            if "audio" in f_name or "audio" in f_label or f_type == "AUDIO":
                a_enabled = True
            if f_type in ("DOCUMENT", "IMAGE", "TEXT") or "file" in f_name or "document" in f_name or "upload" in f_name:
                f_enabled = True

    else:
        # Default fallback for new competitions where admin did not specify toggles:
        # By default, keep file upload enabled.
        f_enabled = True
        v_enabled = False
        a_enabled = False

    # Validation: at least one submission format must be enabled
    if not (f_enabled or v_enabled or a_enabled):
        raise HTTPException(
            status_code=400,
            detail="At least one submission format (File upload, Video link, or Audio upload) must be enabled for this competition."
        )

    # Generate or sync dynamic_submission_fields
    final_fields: List[Dict[str, Any]] = []

    dyn_matches = False
    if has_dyn:
        has_doc = any(
            (f.get("type") in ("DOCUMENT", "IMAGE", "TEXT") or "file" in str(f.get("name", "")).lower())
            for f in raw_dyn if isinstance(f, dict)
        )
        has_vid = any(
            (f.get("type") == "VIDEO" or "video" in str(f.get("name", "")).lower())
            for f in raw_dyn if isinstance(f, dict)
        )
        has_aud = any(
            (f.get("type") == "AUDIO" or "audio" in str(f.get("name", "")).lower())
            for f in raw_dyn if isinstance(f, dict)
        )
        # Check if raw_dyn contradicts the enabled toggles
        if (has_doc and not f_enabled) or (has_vid and not v_enabled) or (has_aud and not a_enabled) or (f_enabled and not has_doc) or (v_enabled and not has_vid) or (a_enabled and not has_aud):
            dyn_matches = False
        else:
            dyn_matches = True

    if has_dyn and dyn_matches:
        # Admin provided custom dynamic fields that align with the enabled toggles
        for f in raw_dyn:
            f_dict = f.model_dump() if hasattr(f, "model_dump") else (dict(f) if isinstance(f, dict) else {})
            final_fields.append(f_dict)
    else:
        # Synthesize standard dynamic fields according to whichever toggles admin enabled
        order = 1
        if f_enabled:
            final_fields.append({
                "name": "file_submission",
                "label": "File Upload",
                "type": FieldType.DOCUMENT.value,
                "description": "Upload document or image entry (.pdf, .png, .jpg, .docx, .zip)",
                "required": True,
                "allowed_formats": ["pdf", "docx", "doc", "png", "jpg", "jpeg", "zip"],
                "max_size_mb": 25.0,
                "display_order": order
            })
            order += 1

        if v_enabled:
            final_fields.append({
                "name": "video_link",
                "label": "YouTube Video Link",
                "type": FieldType.VIDEO.value,
                "description": "Provide a valid YouTube link (e.g. https://www.youtube.com/watch?v=... or https://youtu.be/...)",
                "required": True,
                "allowed_formats": ["youtube"],
                "max_size_mb": None,
                "display_order": order
            })
            order += 1

        if a_enabled:
            final_fields.append({
                "name": "audio_file",
                "label": "MP3 Audio Upload",
                "type": FieldType.AUDIO.value,
                "description": "Upload an MP3 audio file (.mp3) only",
                "required": True,
                "allowed_formats": ["mp3"],
                "max_size_mb": 20.0,
                "display_order": order
            })
            order += 1

    # Build human-readable descriptions and list of allowed submission types
    allowed_types = []
    desc_parts = []
    if f_enabled:
        allowed_types.append("FILE")
        desc_parts.append("File Upload (PDF/Images/Docs)")
    if v_enabled:
        allowed_types.append("VIDEO_LINK")
        desc_parts.append("YouTube Video Link")
    if a_enabled:
        allowed_types.append("AUDIO")
        desc_parts.append("MP3 Audio File Upload")

    submission_format_config = {
        "allow_file_upload": f_enabled,
        "allow_video_link": v_enabled,
        "allow_audio_upload": a_enabled,
        "allowed_submission_types": allowed_types,
        "enabled_fields": [f.get("name") for f in final_fields if isinstance(f, dict)],
        "require_all_fields": False if (v_enabled and a_enabled and not f_enabled) else (len(allowed_types) <= 1),
        "description": f"Allowed submission types: {', '.join(desc_parts)}" if desc_parts else "No submission format configured."
    }

    # Assign normalized values back to data
    data["allow_file_upload"] = f_enabled
    data["allow_video_link"] = v_enabled
    data["allow_audio_upload"] = a_enabled
    data["submission_formats"] = allowed_types
    data["submission_format_config"] = submission_format_config
    data["dynamic_submission_fields"] = final_fields

    return data


def enrich_competition_submission_config(comp: Dict[str, Any]) -> Dict[str, Any]:
    """
    Ensures that any competition document read from MongoDB has the complete,
    consistent submission format configuration, even if it was created before
    explicit submission toggles were added.
    """
    if not comp or not isinstance(comp, dict):
        return comp

    if "submission_format_config" in comp and comp["submission_format_config"] is not None:
        cfg = comp["submission_format_config"]
        comp["allow_file_upload"] = comp.get("allow_file_upload", cfg.get("allow_file_upload", True))
        comp["allow_video_link"] = comp.get("allow_video_link", cfg.get("allow_video_link", False))
        comp["allow_audio_upload"] = comp.get("allow_audio_upload", cfg.get("allow_audio_upload", False))
        return comp

    # If missing, normalize non-destructively
    temp = dict(comp)
    try:
        normalized = normalize_submission_settings(temp, is_update=False)
        comp["allow_file_upload"] = normalized.get("allow_file_upload", True)
        comp["allow_video_link"] = normalized.get("allow_video_link", False)
        comp["allow_audio_upload"] = normalized.get("allow_audio_upload", False)
        comp["submission_formats"] = normalized.get("submission_formats", ["FILE"])
        comp["submission_format_config"] = normalized.get("submission_format_config")
        if not comp.get("dynamic_submission_fields"):
            comp["dynamic_submission_fields"] = normalized.get("dynamic_submission_fields", [])
    except Exception:
        # Fallback safe defaults
        comp["allow_file_upload"] = comp.get("allow_file_upload", True)
        comp["allow_video_link"] = comp.get("allow_video_link", False)
        comp["allow_audio_upload"] = comp.get("allow_audio_upload", False)
        comp["submission_formats"] = ["FILE"] if comp["allow_file_upload"] else []
        comp["submission_format_config"] = {
            "allow_file_upload": comp["allow_file_upload"],
            "allow_video_link": comp["allow_video_link"],
            "allow_audio_upload": comp["allow_audio_upload"],
            "allowed_submission_types": comp["submission_formats"],
            "description": "Default submission format configuration"
        }

    return comp
