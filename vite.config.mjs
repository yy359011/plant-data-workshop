import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

const apiPort = Number(process.env.PLANT_API_PORT || 8766);
const devPort = Number(process.env.PLANT_DEV_PORT || 5173);

export default defineConfig({
  // 相对路径，兼容部署到 GitHub Pages 子路径
  base: "./",
  build: {
    outDir: "dist/client",
  },
  resolve: {
    // 彻底阻止 Vite 对 pdfjs-dist 做任何处理——我们用 public/ 下的 UMD script
    alias: {
      "pdfjs-dist": new URL("./src/stubs/pdfjs-empty.ts", import.meta.url).pathname,
    },
  },
  optimizeDeps: {
    include: ["vue"],
    exclude: ["pdfjs-dist"],
  },
  server: {
    host: process.env.PLANT_DEV_HOST || "127.0.0.1",
    port: devPort,
    strictPort: true,
    proxy: {
      "/api": {
        target: `http://127.0.0.1:${apiPort}`,
        changeOrigin: true,
        xfwd: true,
        configure(proxy) {
          proxy.on("proxyReq", (proxyReq, req) => {
            // Vite is the only trusted proxy: discard client-supplied forwarding
            // claims, including attempts to impersonate localhost during setup.
            proxyReq.setHeader("X-Forwarded-For", req.socket.remoteAddress || "");
            proxyReq.setHeader("X-Forwarded-Host", req.headers.host || "");
            proxyReq.setHeader("X-Forwarded-Proto", req.socket.encrypted ? "https" : "http");
          });
        },
      },
    },
    allowedHosts: ["terminal.local"],
    warmup: {
      clientFiles: ["./src/main.ts"],
    },
  },
  plugins: [vue()],
});
