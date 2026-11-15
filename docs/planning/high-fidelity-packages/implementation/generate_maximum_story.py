import copy, json, shutil
from pathlib import Path
import yaml
from jarvis import package_story
from jarvis.presentation import _deck, _targets
from jarvis.presentation_source import read_source
out=Path('docs/planning/high-fidelity-packages/implementation/maximum-story')
source=out/'source'
source.mkdir(parents=True,exist_ok=True)
original=Path('examples/presentations/street')
for image in original.glob('*.svg'): shutil.copy2(image,source/image.name)
data=yaml.safe_load((original/'presentation.yaml').read_text())
data['id']='maximum-presentation'; data['title']='Maximum supported presentation'; data['decisions']=[]
opening=copy.deepcopy(data['slides'][0]);data['slides']=[]
for n in range(package_story.MAX_SLIDES-2):
    slide=copy.deepcopy(opening); suffix=f'-repeat-{n}';slide['id']+=suffix; mapping={}
    for obj in slide['objects']:
        mapping[obj['id']]=obj['id']+suffix;obj['id']+=suffix
        for paragraph in obj['paragraphs']:
            mapping[paragraph['id']]=paragraph['id']+suffix;paragraph['id']+=suffix
            for run in paragraph['runs']:
                mapping[run['id']]=run['id']+suffix;run['id']+=suffix
    slide['readingOrder']=[mapping[id] for id in slide['readingOrder']]
    slide['narration']=[{'id':f'performance-cue-{n}','say':'This repeated opening measures the supported story limit.','focus':[slide['objects'][0]['id']]}]
    data['slides'].append(slide)
path=source/'presentation.yaml';path.write_text(yaml.safe_dump(data,sort_keys=False))
parsed=read_source(path);deck=_deck(parsed,_targets(parsed));deck['name']='maximum-existing'
for slide in deck['slides']:
    slide['say']=slide.pop('beats')[0]['say']
(source/'content.json').write_text(json.dumps(deck,indent=2))
print(path)
