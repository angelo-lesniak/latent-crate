# Official template tools

LatentCrate can inspect the official workflow templates installed in a selected
ComfyUI image. It does not read a changing online catalog.

## List local-compatible templates

```bash
bash bin/latentcrate templates list current
bash bin/latentcrate templates list edge
```

The first use builds the selected image if needed. The inspection itself runs
offline. The list contains installed templates whose packaged metadata says:

- `openSource` is true;
- status is active or not set;
- `includeOnDistributions` is not set or includes `local`;
- the template is not in the official API-workflow bundle.

This is a local-compatibility candidate list, not a promise that a workflow is
ready on this computer. The table shows declared extra node packages. It does
not confirm that those nodes or the required model files are installed. A live
ComfyUI node check would be needed to prove that every workflow node is
available and is not an API node.

Use the value in the `TEMPLATE ID` column with the next command.

## Create a model-set manifest draft

```bash
bash bin/latentcrate templates create-model-set video_minimax_h3_i2v edge
bash bin/latentcrate templates create-model-set image_example current \
  --name my-model-set
```

The command reads embedded model hints from the selected workflow and creates:

```text
build/model-set-drafts/<name>.toml
```

It never overwrites an existing draft. The draft directory is ignored by Git.

Official templates often name a Hugging Face repository, file, destination,
and a moving branch such as `main`. They may omit exact sizes, checksums, and
licenses. LatentCrate marks those missing values with `TODO` instead of
guessing them. A draft is intentionally rejected by `models fetch`.

Before adding the file to `config/model-sets/`:

1. confirm every repository and ComfyUI destination;
2. replace branch names with full 40-character repository commits;
3. add exact byte sizes and lowercase SHA-256 checksums;
4. add immutable license links;
5. pin the official workflow link to a full commit;
6. run `python3 scripts/verify-model-set-metadata.py` and the model-set tests.

The current model-set format accepts Hugging Face files only. Unsupported model
URLs remain as comments in the draft so they are not silently lost.
