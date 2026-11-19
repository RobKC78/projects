import { sampleCameraMove } from './camera.mjs';
import { writeFileSync } from 'node:fs';
const moves = [{id:'florida',from:[14,-34,9600000],to:[26.3,-84.4,1250000]}, {id:'hawaii',from:[21,-93,9600000],to:[19.42,-155.29,700000]}];
const pose=([lat,lon,alt])=>({lat,lon,alt,heading:0,pitch:-90,roll:0});
for(const {id,from,to} of moves){const move={from:pose(from),to:pose(to),easing:'cubic-in-out'};writeFileSync(new URL('./'+id+'.json',import.meta.url),JSON.stringify(Array.from({length:240},(_,i)=>sampleCameraMove(move,Math.min(1,i/178)))));}
