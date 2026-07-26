"""Local-disk photo/PDF storage, laid out the same way the proposed bucket is.

Swapping this for Supabase Storage/S3 in Phase 1 is meant to be a change to
this file only -- everything else calls save_photo()/incident_dir() and
doesn't know whether the bytes end up on disk or in a bucket.
"""
