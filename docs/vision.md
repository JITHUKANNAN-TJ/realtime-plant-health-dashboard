# Vision roadmap: leaf-disease detection (deferred)

**Status: not implemented.** The current release is a sensor and analytics project.
This document records the design so the feature can be added later without reshaping
the backend.

## Why it is deferred

- No PyTorch/Ultralytics/TFLite runtime is installed, and the project deliberately
  avoids heavy dependencies for the core dashboard.
- There is no labelled disease dataset in the repository, and training needs a GPU
  session plus validation time.
- Sensor data, offline buffering, alerts and the PWA are the primary deliverables;
  a weak classifier would hurt trust more than help.

The backend already has the right seams: SQLite storage, an event journal and a
static PWA. Vision can be added as an optional module without touching ingest.

## Goal

Let the user photograph a leaf and get a ranked set of likely conditions with a
confidence score and a short, non-prescriptive note. It is a triage aid, not a
diagnosis.

## Proposed design

1. **Capture** - phone camera first (file upload in the PWA). An ESP32-CAM is an
   optional later upgrade; it must not block the sensor node.
2. **Quality gate** - reject blurry/dark frames (Laplacian variance + exposure
   histogram) before inference.
3. **Inference** - a small image classifier (MobileNetV3 or YOLOv8n-cls) fine-tuned
   on a plant-disease dataset, exported to ONNX and run with `onnxruntime` on the PC.
   The ESP32 never runs the model.
4. **Store** - `vision_scans` table (`ts`, `device`, `image_path`, `top_label`,
   `confidence`, `model_version`, `scores_json`). Images live under
   `data/scans/` and are gitignored.
5. **Present** - a "Scan leaf" panel in the dashboard and a line in the weekly
   report. Results are advisory and always show the model version.

## Dataset notes

- PlantVillage is clean and lab-like; models trained on it often fail on real
  phone photos. PlantDoc is closer to field conditions but smaller and noisier.
- Check and record the licence of whatever dataset is used.
- Split by plant/condition and capture device where possible; a random split
  overstates accuracy.

## API sketch

```text
POST /api/vision/predict   multipart image  -> { scan_id, top: {label, confidence}, scores: [...] }
GET  /api/vision/scans     -> recent scans
GET  /api/vision/scans/<id>/image
DELETE /api/vision/scans/<id>
```

## Milestones

1. Offline script that classifies a folder of images and prints a confusion matrix.
2. Upload endpoint + quality gate + storage; no UI yet.
3. Dashboard panel with image preview and top-3 labels.
4. Optional ESP32-CAM capture with an explicit opt-in build flag.
5. Weekly report section and retention policy for images.

## Acceptance criteria

- Top-3 accuracy and per-class recall reported on a held-out, field-like set.
- Inference under ~1 s per image on the existing PC.
- No image or model download happens at runtime; no telemetry.
- Every result is labelled "advisory, not a diagnosis".

## Non-goals

- Real-time video or on-device training.
- Cloud inference or uploading user photos to third parties.
- Pesticide/treatment recommendations.
