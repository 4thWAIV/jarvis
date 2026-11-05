from pathlib import Path
import subprocess
base=Path(__file__).resolve().parent
root=base.parents[2]
bundle=base/'bundle.tmp.js'
try:
 subprocess.run([str(root/'frontend/node_modules/.bin/esbuild'),str(base/'source/scales.ts'),'--bundle','--minify','--format=iife',f'--outfile={bundle}'],check=True)
 html=(base/'source/template.html').read_text().replace('BUNDLE',bundle.read_text().replace('</script','<\\/script'))
 (base/'index.html').write_text(html)
finally:
 bundle.unlink(missing_ok=True)
