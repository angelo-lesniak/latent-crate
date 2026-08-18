# Trainer integrations

Training integrations are deferred until they can be shipped as complete,
independently testable services.

Planned layout:

```text
services/
  comfy/
  kohya/
  ai-toolkit/
  musubi/
```

Each trainer will own its image, upstream reference, Python environment, and
compiled caches. Services may share only a predictable mount vocabulary:

```text
/models
/datasets
/outputs
/config
/cache
```

Do not construct a universal trainer image. Kohya, ai-toolkit, and Musubi have
different dependency and PyTorch release schedules. TensorBoard, when added, should be a
pinned CPU-only optional service with a localhost port and read-only log mount.

Every trainer must use an explicit Compose profile and service-specific wrapper
command. The ComfyUI startup and shutdown code names the `comfy` service directly so adding a
trainer cannot make a normal `latentcrate up` reserve another GPU accidentally.
