import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = fileURLToPath(new URL('../../', import.meta.url));
const out = path.join(root, 'frontend/public/assets/manual');
const files = ['docs/离线RAG运行说明.md','docs/模型配置设置.md','手动验收/验证步骤.md','手动验收/待上传/LY317 竹节斜纹面料规格书.md'];
for (const file of files) {
  const target = path.join(out,file);
  await mkdir(path.dirname(target), { recursive:true });
  await writeFile(target,await readFile(path.join(root,file)));
}
const html = await readFile(path.join(root,'使用说明.html'),'utf8');
await writeFile(path.join(out,'index.html'),html.replace('href="http://127.0.0.1:8766/"','href="/"'));
