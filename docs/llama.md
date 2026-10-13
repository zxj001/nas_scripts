# llama.cpp on the GPU (VM)

Runs llama.cpp with Vulkan on the GPU passed through to the Debian 13 VM. On `myllama` (VM 100)
that's the R9 Nano, which first needs the patched amdgpu from [fiji-amdgpu.md](fiji-amdgpu.md).
Check that `vulkaninfo --summary` lists the GPU (`RADV FIJI` on the R9 Nano) before you start.

```
scripts/llama_setup.sh install   # llama.cpp Vulkan + CPU builds under ~/llama
scripts/llama_setup.sh models    # Qwen3 1.7B Q8_0, Qwen3 4B and Qwen3.5 4B Q4_K_M, SHA-256 checked
scripts/llama_setup.sh check     # GPU listed, and a short GPU generation reads as English
scripts/llama_setup.sh bench     # GPU vs CPU tables in ~/llama/results/
scripts/llama_setup.sh bench --depth 8192,16384 --kv q8_0 Qwen3.5-4B-Q4_K_M.gguf
                                 # GPU at long contexts, with peak VRAM/GTT
```

None of it needs root. The only packages it needs are `libvulkan1` and `mesa-vulkan-drivers`
(plus `vulkan-tools` for `vulkaninfo`), and `install` names them when they are missing.

- **Builds:** llama.cpp's prebuilt Ubuntu x64 releases, pinned in the script (`BUILD_DEFAULT`).
  `install --build bNNNNN` tries a newer release, and `~/llama/current` points at the build in use.
- **CPU baseline:** `bench` compares against the separate CPU-only build. The Vulkan build still
  offloads large prompt batches to the GPU at `-ngl 0`, so it doesn't give a true CPU number.
- **Models:** `model REPO FILE` fetches any other GGUF, e.g.
  `scripts/llama_setup.sh model Qwen/Qwen3-8B-GGUF Qwen3-8B-Q4_K_M.gguf`. Keep GPU models under
  about 3.5 GiB on a 4 GiB card, so the context has room.

To serve a model over HTTP (an OpenAI-compatible API on port 8080), use Qwen3.5-4B. It fits a
16k context in VRAM when the KV cache is q8_0 ([long contexts](#long-contexts-qwen3-4b-vs-qwen35-4b)):

```
cd ~/llama/current/vulkan && ./llama-server -m ~/llama/models/Qwen3.5-4B-Q4_K_M.gguf -ngl 99 \
    -c 16384 -ctk q8_0 -ctv q8_0 --host 0.0.0.0
```

Up to about 8k of context, leave out `-ctk q8_0 -ctv q8_0`: the default f16 cache was faster there.

## Results on myllama

Measured 2026-10-12 with llama.cpp b11577 and `llama-bench` (3 repetitions). The GPU is the R9 Nano
(RADV FIJI, Mesa 25.0.7) with all layers offloaded. The CPU baseline is 4 vCPUs of a Xeon E3-1270L v4.
Units are tokens per second.

| Model | Test | GPU | CPU | GPU speed-up |
| --- | --- | ---: | ---: | ---: |
| Qwen3-1.7B Q8_0 | prompt, 512 tokens (pp512) | 1143 | 54.0 | 21× |
| Qwen3-1.7B Q8_0 | generation, 128 tokens (tg128) | 97.2 | 11.4 | 8.6× |
| Qwen3-4B Q4_K_M | prompt, 512 tokens (pp512) | 441 | 36.4 | 12× |
| Qwen3-4B Q4_K_M | generation, 128 tokens (tg128) | 51.9 | 8.1 | 6.4× |
| Qwen3.5-4B Q4_K_M | prompt, 512 tokens (pp512) | 429 | 31.4 | 14× |
| Qwen3.5-4B Q4_K_M | generation, 128 tokens (tg128) | 48.5 | 5.8 | 8.4× |

The full `llama-bench` output is in [llama-results/](llama-results/). A greedy GPU generation
(`check`) produced correct, coherent text, so the Vulkan shaders compute correctly on GCN3 and
aren't just fast.

## Long contexts: Qwen3-4B vs Qwen3.5-4B

Qwen3.5-4B (`unsloth/Qwen3.5-4B-GGUF`) uses linear attention (Gated DeltaNet) in 3 of every 4
layers. So only 8 of its 32 layers keep a KV cache: 32 KiB per token, against Qwen3-4B's
144 KiB (36 layers). At 16k tokens that is 0.5 GiB instead of 2.25 GiB, and on a 4 GiB card that
decides whether the model stays in VRAM. RADV runs the DeltaNet layers on Fiji without falling
back to the CPU. The greedy `check` generation is coherent too.

Both models at Q4_K_M, all layers on the GPU, 512-token prompt / 128-token generation, in tokens
per second. "Spills" means the peak GTT (system RAM used by the GPU) rose well above its ~550 MiB
idle level.

| Context in use | Qwen3-4B prompt | Qwen3-4B generation | Qwen3.5-4B prompt | Qwen3.5-4B generation |
| --- | ---: | ---: | ---: | ---: |
| none | 441 | 52 | 429 | 48 |
| 4k | 246–250 | 46 | 297–376 | 22–44 |
| 8k | 96 | 19–38 | 338 | 42 |
| 16k, f16 KV (default) | 78–80 | 4.8, spills | 277–288 | 7–8, spills |
| 16k, q8_0 KV | 95 | 4.8, spills | 288 | **35**, in VRAM |
| 32k, q8_0 KV | – | – | 185 | 7.7, spills |

Ranges are repeated runs that disagreed.

At short contexts, Qwen3-4B is about 5% faster because it is smaller (2.32 vs 2.54 GiB). From 4k on,
Qwen3.5-4B processes prompts much faster (3.5× at 8k). At 16k, only Qwen3.5 with a q8_0 KV cache
stays in VRAM. There it generates 35 t/s, while Qwen3-4B spills and drops to under 5. Generation
speed at long contexts varies between runs by up to 2×: the card exposes a 3.75 GiB device-local
heap, and how much of the context the driver places in VRAM changes from run to run. Prompt
processing speeds are stable. Raw output, with the peak memory of each run, is in
[llama-results/](llama-results/).
