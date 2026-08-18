import { mkdir, writeFile } from "node:fs/promises";

await mkdir("dist", { recursive: true });
await writeFile("dist/index.html", "<!doctype html><title>LatentCrate fixture</title>\n");
