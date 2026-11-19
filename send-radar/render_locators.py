"""Render a regional locator using Natural Earth land and precomputed director camera poses."""
import argparse
import json
import math
from pathlib import Path
import subprocess
import numpy as np
from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 1280, 720
TAU = 2 * math.pi
HERE = Path(__file__).resolve().parent
ROUTES = {
    "florida": ((26.3, -84.4), "WEST FLORIDA", "West Florida Escarpment", "Approximate region of the ROV encounter"),
    "hawaii": ((19.42, -155.29), "HAWAIʻI", "Kīlauea summit", "Hawaiian Volcano Observatory"),
}

def make_texture(path):
    chart = Image.new("L", (3600, 1800), 0)
    draw = ImageDraw.Draw(chart)
    for feature in json.loads(path.read_text())["features"]:
        geom = feature["geometry"]
        polygons = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        for polygon in polygons:
            for j, ring in enumerate(polygon):
                points = [((lon+180)*10, (90-lat)*10) for lon,lat,*_ in ring]
                draw.polygon(points, fill=255 if j == 0 else 0)
    return np.asarray(chart)

def load_font(size, strong=False):
    known = "DejaVuSans-Bold.ttf" if strong else "DejaVuSans.ttf"
    return ImageFont.truetype(known, size)

def render(name, geography, frames, out):
    (target_lat,target_lon),region,title,detail = ROUTES[name]
    yy,xx=np.mgrid[:HEIGHT,:WIDTH].astype(np.float32)
    mask=make_texture(geography)
    sky=np.zeros((HEIGHT,WIDTH,3),np.uint8)
    sky[:]=[5,15,23]
    rng=np.random.default_rng(102)
    for _ in range(235):
        x,y=rng.integers(WIDTH),rng.integers(HEIGHT); sky[y,x]=rng.integers(43,103)
    font_big, font_mid, font_small = load_font(45,True),load_font(23),load_font(16)
    cmd=["ffmpeg","-y","-v","error","-f","rawvideo","-pix_fmt","rgb24","-s","1280x720","-r","24","-i","pipe:0","-an","-threads","2","-c:v","libx264","-preset","veryfast","-crf","19","-pix_fmt","yuv420p","-movflags","+faststart",str(out)]
    enc=subprocess.Popen(cmd,stdin=subprocess.PIPE)
    for idx,cam in enumerate(frames):
        lon0,lat0=map(math.radians,[cam["lon"],cam["lat"]]); scale=7600000/(cam["alt"]+4500000);radius=470*scale
        cx,cy=790,341
        x=(xx-cx)/radius; y=(cy-yy)/radius; r2=x*x+y*y; inside=r2<=1
        z=np.sqrt(np.maximum(0,1-r2))
        lat=np.arcsin(np.clip(y*math.cos(lat0)+z*math.sin(lat0),-1,1))
        lon=lon0+np.arctan2(x,z*math.cos(lat0)-y*math.sin(lat0))
        u=((np.degrees(lon)+180)*10).astype(int)%3600;v=np.clip(((90-np.degrees(lat))*10).astype(int),0,1799)
        land=mask[v,u]>.5
        idx_grid=(np.abs((np.degrees(lat)+5)%10-5)<.12)|(np.abs((np.degrees(lon)+5)%10-5)<.12)
        light=np.maximum(.14,-.34*x+.55*y+.76*z)
        limb=(1-z)**3
        shade=np.zeros_like(sky)
        for j, (o,l,a) in enumerate([(17,104,37),(63,158,99),(87,147,119)]):
            shade[:,:,j]=np.clip((np.where(land,l,o)*(.42+.67*light)+a*.59*limb+idx_grid*(10 if j==0 else 17)),0,255)
        rgb=sky.copy();rgb[inside]=shade[inside]
        im=Image.fromarray(rgb);d=ImageDraw.Draw(im)
        dt=math.radians(target_lon)-lon0;lt=math.radians(target_lat)
        px=cx+radius*math.cos(lt)*math.sin(dt);py=cy-radius*(math.cos(lat0)*math.sin(lt)-math.sin(lat0)*math.cos(lt)*math.cos(dt))
        front=math.sin(lat0)*math.sin(lt)+math.cos(lat0)*math.cos(lt)*math.cos(dt)>0
        if front:
            r=7+15*((idx%36)/36);d.ellipse((px-r,py-r,px+r,py+r),outline='#82fae0',width=2);d.ellipse((px-4,py-4,px+4,py+4),fill='#fff3bc')
        # Solid side panel keeps project and footage credits readable on phone screens.
        d.rounded_rectangle((42,208,424,441),18,fill='#102a35',outline='#3b666c',width=2)
        d.text((71,232),'SEND RADAR  /  REGIONAL LOCATOR',font=font_small,fill='#88dfcf')
        d.text((69,281),region,font=font_big,fill='#fff2d9');d.text((73,350),title,font=font_mid,fill='white');d.text((73,400),detail,font=font_small,fill='#a3c4c5')
        d.text((54,633),'Natural Earth geography  •  Camera motion: God’s Eye View / Bilawal Sidhu',font=font_small,fill='#d3e9df',stroke_width=1,stroke_fill='#0b1d26')
        enc.stdin.write(im.tobytes())
    enc.stdin.close()
    if enc.wait():raise RuntimeError("Locator encoding failed")

if __name__=="__main__":
    ap=argparse.ArgumentParser();ap.add_argument("route",choices=ROUTES);ap.add_argument("land",type=Path);ap.add_argument("output",type=Path);a=ap.parse_args()
    render(a.route,a.land,json.loads((HERE/(a.route+".json")).read_text()),a.output)
