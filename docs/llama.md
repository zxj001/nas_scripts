# llama.cpp on the GPU (VM)

Runs llama.cpp with Vulkan on the GPU passed through to the Debian 13 VM. On `myllama` (VM 100)
that's the R9 Nano, which first needs the patched amdgpu from [fiji-amdgpu.md](fiji-amdgpu.md).
Check that `vulkaninfo --summary` lists the GPU (`RADV FIJI` on the R9 Nano) before you start.

```
scripts/llama_setup.sh install   # llama.cpp Vulkan + CPU builds under ~/llama
scripts/llama_setup.sh models    # Qwen3 1.7B Q8_0 and 4B Q4_K_M, SHA-256 checked
scripts/llama_setup.sh check     # GPU listed, and a short GPU generation reads as English
scripts/llama_setup.sh bench     # GPU vs CPU tables in ~/llama/results/
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

To serve a model over HTTP (an OpenAI-compatible API on port 8080):

```
cd ~/llama/current/vulkan && ./llama-server -m ~/llama/models/Qwen3-4B-Q4_K_M.gguf -ngl 99 --host 0.0.0.0
```

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

The full `llama-bench` output is in [llama-results/](llama-results/). A greedy GPU generation
(`check`) produced correct, coherent text, so the Vulkan shaders compute correctly on GCN3 and
aren't just fast.
