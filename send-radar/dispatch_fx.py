"""Frame-based, reproducible editorial effects. All geometry is original."""
import functools
import hashlib
import json
import math
import random
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageOps, ImageFilter, ImageChops
from scipy.spatial import Delaunay
from scipy.signal import butter, sosfilt

MODES = ('cannon', 'whirlpool', 'hot_seam', 'prism', 'iris', 'shutters')
PILOT = ('cannon', 'whirlpool', 'hot_seam', 'prism')
COLORS = {'cannon': (91,223,255), 'whirlpool': (75,225,202), 'hot_seam': (255,137,49),
          'prism': (202,153,255), 'iris': (116,225,222), 'shutters': (192,191,255)}
DURATION = 3.9
TAKE = Path(__file__).with_name('transition-cast.json')

def cast(episode, count=4, previous=None):
    """Shuffle without replacement, save the casting in the production manifest."""
    rng=random.Random(int.from_bytes(hashlib.sha256(episode.encode()).digest()[:8],'big'))
    deck=[]; choices=list(MODES)
    while len(deck)<count:
        rng.shuffle(choices)
        if choices[0]==(deck[-1] if deck else previous): choices[0],choices[1]=choices[1],choices[0]
        deck.extend(choices)
    return deck[:count]

def selected():
    manifest=json.loads(TAKE.read_text())
    return manifest.get('casts') or cast(manifest['episode'],manifest.get('chapters',4),manifest.get('previous'))

def recap_mode():
    manifest=json.loads(TAKE.read_text());used=selected()
    requested=manifest.get('recap')
    if requested in MODES and requested not in used:return requested
    available=[mode for mode in cast(manifest['episode']+' recap',len(MODES)) if mode not in used]
    return available[0] if available else next(mode for mode in MODES if mode!=used[-1])

def clamp(t): return max(0.,min(1.,t))
def ease(t):
    v=clamp(t); return v*v*(3-2*v)
def interp(a,b,p): return a+(b-a)*p
def box(x,y,r): return (x-r,y-r,x+r,y+r)
def addlight(im, ink):
    small=ink.resize((max(32,ink.width//3),max(32,ink.height//3)),Image.Resampling.BILINEAR)
    glow=small.filter(ImageFilter.GaussianBlur(3)).resize(im.size,Image.Resampling.BILINEAR)
    return ImageChops.add(ImageChops.add(im,glow),ink)
def line(d,pts,color,thick):
    d.line(pts,fill=color,width=max(1,round(thick)),joint='curve')
def cover(im,sz): return ImageOps.fit(im,sz,method=Image.Resampling.BILINEAR)
def screen(im,subject,t,c,accent,font):
    w,h=im.size; wall=im.copy()
    # A glass display with a physical frame gives Radar a surface to interact with.
    x0,y0,x1,y1=map(int,(w*.052,h*.19,w*.66,h*.77))
    tex=cover(subject,(x1-x0,y1-y0)); wall.paste(tex,(x0,y0))
    d=ImageDraw.Draw(wall)
    d.rectangle((x0-3,y0-3,x1+3,y1+3),outline='#162830',width=5)
    d.rectangle((x0-5,y0-5,x1+5,y1+5),outline='#789297',width=2)
    L=w*.042
    for x,y,sx,sy in [(x0,y0,1,1),(x1,y0,-1,1),(x0,y1,1,-1),(x1,y1,-1,-1)]:
        line(d,[(x+sx*L,y),(x,y),(x,y+sy*L)],accent,3)
    fs=max(12,round(w*.015))
    d.text((x0,y0-fs-10),'INCOMING FIELD CALL',font=font(fs,'semi'),fill='#e1f6f1')
    for j in range(27):
        y=y1+18; hh=(2+9*abs(math.sin(t*7-j*.6)*math.sin(t*2+j*.45)))*w/950
        line(d,[(x0+j*w*.009,y-hh/2),(x0+j*w*.009,y+hh/2)],accent,2)
    return wall

@functools.lru_cache(maxsize=8)
def triangles(w,h):
    rng=random.Random(83); origin=np.array((.35*w,.45*h))
    pts=[(0,0),(w-1,0),(w-1,h-1),(0,h-1)]
    for x in np.linspace(0,w-1,8)[1:-1]: pts.extend([(x,0),(x,h-1)])
    for y in np.linspace(0,h-1,6)[1:-1]: pts.extend([(0,y),(w-1,y)])
    for j in range(4):
        for i in range(7):pts.append(((i+.5+rng.uniform(-.27,.27))*w/7,(j+.5+rng.uniform(-.23,.23))*h/4))
    for r,n in [(w*.027,8),(w*.086,12)]:
        for j in range(n):
            a=j*math.tau/n+rng.uniform(-.13,.13); pts.append(tuple(origin+r*np.array([math.cos(a),.8*math.sin(a)])))
    pts.append(tuple(origin)); a=np.array(pts); out=[]
    for ids in Delaunay(a).simplices:
        v=a[ids];ctr=v.mean(0); delta=ctr-origin; length=np.linalg.norm(delta); delta/=max(1,length)
        lo=np.floor(v.min(0)).astype(int); hi=np.ceil(v.max(0)).astype(int)+1
        poly=[tuple(p-lo) for p in v]; out.append((tuple(lo),tuple(hi),poly,tuple(delta),length/w,rng.uniform(-170,170),rng.uniform(.85,1.35)))
    return out

def fracture(old,field,age):
    w,h=old.size; result=field.copy()
    for lo,hi,poly,delta,dist,spin,speed in triangles(w,h):
        q=age-dist*.32
        if q>1.35:continue
        tile=old.crop((*lo,*hi)).convert('RGBA'); mask=Image.new('L',tile.size)
        md=ImageDraw.Draw(mask); md.polygon(poly,fill=255); tile.putalpha(mask)
        if q>-.11:
            color=(int(110+120*max(0,1-abs(q)*2)),220,243,255)
            ImageDraw.Draw(tile).line(poly+[poly[0]],fill=color,width=max(1,round(w*.0024)))
        if q<=0: result.paste(tile,lo,tile);continue
        fac=1+.58*q
        size=(max(1,round(tile.width*fac*max(.065,abs(math.cos(q*(2.8+speed)))))),max(1,round(tile.height*fac)))
        tile=tile.resize(size,Image.Resampling.BILINEAR).rotate(spin*q,Image.Resampling.BILINEAR,expand=True)
        if q>.72:tile.putalpha(tile.getchannel('A').point(lambda a:a*clamp((1.34-q)/.62)))
        x=(lo[0]+hi[0])/2+delta[0]*w*(.08*q+.70*q*q)*speed
        y=(lo[1]+hi[1])/2+delta[1]*h*(.15*q+1.25*q*q)*speed+.23*h*q*q
        result.paste(tile,(round(x-tile.width/2),round(y-tile.height/2)),tile)
    return result

@functools.lru_cache(maxsize=4)
def grid(w,h):
    y,x=np.mgrid[0:h,0:w].astype(np.float32);cx=w*.35;cy=h*.45
    dx=x-cx;dy=y-cy
    return x,y,dx,dy,np.hypot(dx,dy)/max(w,h),np.arctan2(dy,dx)

def whirl(old,field,p):
    w,h=old.size;x,y,dx,dy,r,a=grid(w,h);s=ease(p)
    d=np.maximum(0,1-r/1.11); ang=3.9*s*d*d
    scale=1+1.8*s*d; cs=np.cos(ang)*scale;sn=np.sin(ang)*scale
    ix=np.clip((w*.35+dx*cs-dy*sn).astype(int),0,w-1);iy=np.clip((h*.45+dx*sn+dy*cs).astype(int),0,h-1)
    olda=np.asarray(old)[iy,ix].astype('float32')
    front=s*1.35;rim=r+.022*np.sin(a*4+9*s)+.012*np.sin(a*9-11*s)-front
    m=np.clip(rim*55+.5,0,1)[...,None]; light=np.exp(-np.abs(rim)*113)[...,None]*(math.sin(math.pi*s)**.55)
    rgb=olda*m+np.asarray(field)*(1-m)+light*np.array([92,204,178],dtype=np.float32)
    return Image.fromarray(np.uint8(np.clip(rgb,0,255)))

def seam(old,field,p):
    w,h=old.size;x,y,_,_,_,_=grid(w,h);s=ease(p);z=y/h
    crease=w*(.38+.12*z+.025*np.sin(13*z)+.017*np.sin(29*z))
    opening=(.006+1.4*s*s)*w*(.26+.74*np.sin(math.pi*(.17+.67*z)))
    depth=x-crease; side=np.sign(depth); distance=np.abs(depth)-opening
    ix=np.clip((x-side*opening).astype(int),0,w-1);iy=np.clip((y+side*s*38*np.sin(z*3)).astype(int),0,h-1)
    skin=np.asarray(old)[iy,ix].astype('float32');f=np.asarray(field)
    m=np.clip(distance/2+.5,0,1)[...,None]
    heat=np.exp(-np.abs(distance)/(w*.011+1))*min(1,s*18+.18)*(1-s**8)
    rgb=skin*m+f*(1-m)+heat[...,None]*np.array([240,123,32],dtype='float32')
    return Image.fromarray(np.uint8(np.clip(rgb,0,255)))

def spectrum(old,field,p):
    w,h=old.size;x,y,*_=grid(w,h);base=np.asarray(field).astype('float32').copy()
    slope=.36*w/h; diag=x+slope*(y-h*.5)
    for i,ink in enumerate([(242,120,93),(128,222,251),(200,144,251)]):
        q=ease((p-.075*i)/.83);cx=(i+.5)*w/3
        stretch=max(.015,abs(math.cos(q*math.pi*.495)))
        shift=(-1 if i==0 else (1 if i==2 else 0))*w*.98*q*q
        lift=(i-1.4)*h*.94*q*q
        srcx=(diag-shift-cx)/stretch+cx-slope*(y-lift-h*.5)
        srcy=y-lift; ix=np.clip(srcx.astype(int),0,w-1);iy=np.clip(srcy.astype(int),0,h-1)
        delta=np.abs((diag-shift-cx)/stretch)
        inside=(delta<=w/6+1)&(srcx>=0)&(srcx<w)&(srcy>=0)&(srcy<h)
        tint=math.sin(q*math.pi)*.32
        base[inside]=np.asarray(old)[iy[inside],ix[inside]]*(1-tint)+np.array(ink)*tint
        edge=(np.abs(delta-w/6)*stretch<max(1,w*.003)) & (srcy>=0)&(srcy<h)
        if .015<q<.985:base[edge]=ink
    return Image.fromarray(np.uint8(np.clip(base,0,255)))

def iris(old,field,p):
    w,h=old.size;x,y,dx,dy,r,a=grid(w,h);s=ease(p)
    blade=np.cos((a+s*1.3+math.pi/6)%(math.pi/3)-math.pi/6)
    v=(r*blade-1.15*s);m=np.clip(v*200+.5,0,1)[...,None]
    glow=np.exp(-np.abs(v)*145)[...,None]*math.sin(math.pi*s)
    return Image.fromarray(np.uint8(np.clip(np.asarray(old)*m+np.asarray(field)*(1-m)+glow*np.array([90,180,180]),0,255)))

def shutters(old,field,p):
    w,h=old.size;out=field.copy();d=ImageDraw.Draw(out)
    for j in range(7):
        y0=round(j*h/7);y1=round((j+1)*h/7);q=ease((p-j*.035)/.78)
        dx=round(w*1.03*q)*(1 if j%2 else -1)
        out.paste(old.crop((0,y0,w,y1)),(dx,y0))
        if .01<q<.99:
            xx=dx if j%2 else w+dx
            line(d,[(xx,y0),(xx,y1)],'#c6f5ff',max(2,w*.004))
    return out

def reveal(mode,wall,field,p):
    if p<=0:return wall.copy()
    if p>=1:return field.copy()
    if mode=='cannon':return fracture(wall,field,p*1.58-.08)
    return {'whirlpool':whirl,'hot_seam':seam,'prism':spectrum,'iris':iris,'shutters':shutters}[mode](wall,field,p)

def action(stage,mode,t,c,perform,landmarks):
    """Stage a gesture, then show the consequence. Coordinates resize for shorts."""
    w,h=stage.size;color=COLORS[mode]; sx=w/960
    pivot=(.35*w,.45*h); age=t-1.5; q=ease(t/1.02)
    launch=ease((t-2.38)/1.12); recoil=math.exp(-((age-.13)/.16)**2)
    anchor=(.78*w,.57*h); size=int(min(w*.49,h*.70))
    if mode=='hot_seam':
        # A rising boot cuts through the wall. Its trajectory is visibly connected to the seam.
        rise=ease((t-.43)/1.05); cx=w*(.77-.28*rise);cy=h*(.62-.38*rise)
        pose='bank';idx=round(10*rise);pivot=(.455*w,.88*h)
    elif mode=='whirlpool':
        catch=ease((t-.1)/.65);haul=ease((t-.9)/.66)
        cx=w*(.77-.105*catch+.13*haul);cy=h*(.59-.075*catch+.03*haul)
        pose='reach' if t<.9 else 'pull';idx=round(10*(catch if t<.9 else haul))
    elif mode=='prism':
        cx=w*(.77-.075*q);cy=h*(.59-.08*q);pose='guide';idx=round(10*q)
    else:
        cx=anchor[0]+w*.07*recoil;cy=anchor[1]-h*.035*recoil
        pose='aim' if age<.06 else 'recoil';idx=round(11*(q if age<.06 else ease(age/.37)))
    if launch:
        cx=interp(cx,-.25*w,launch);cy-=.26*h*math.sin(math.pi*launch);size=max(40,int(size*(1-.40*launch)))
        pose='bank';idx=round(11*launch)
    if t>3.63:return stage
    name=pose+f'{idx:02d}'
    hand=perform(stage,name,'X',(cx,cy),size,t,c,0,None,.2+launch*2, 'shocked' if mode=='whirlpool' and age>.04 and age<.75 else 'normal')
    fx=Image.new('RGB',stage.size);d=ImageDraw.Draw(fx)
    def ring(pt,r,col,width):
        if r>0:d.ellipse(box(*pt,r),outline=col,width=max(1,round(width*sx)))
    def star(pt,r,col,num=8,rot=0):
        pts=[(pt[0]+math.cos(rot+a*math.pi/num)*r*(1 if a%2==0 else .30),pt[1]+math.sin(rot+a*math.pi/num)*r*(1 if a%2==0 else .30)) for a in range(num*2)]
        d.polygon(pts,fill=col)
    if mode=='cannon' and t<2.22:
        # A telescoping cartoon gauntlet, drawn along the axis from the actual wrist.
        a=np.array(hand); v=np.array(pivot)-a;v/=max(1,np.linalg.norm(v)); normal=np.array([-v[1],v[0]])
        extend=ease(t/.6); L=size*(.045+.16*extend); rr=size*.039; hull=ImageDraw.Draw(stage)
        def seg(b,e,r1,r2,fill):
            hull.polygon([tuple(a+b*v-r1*normal),tuple(a+e*v-r2*normal),tuple(a+e*v+r2*normal),tuple(a+b*v+r1*normal)],fill=fill)
        seg(-rr*.55,L,rr*.79,rr,'#1a2a37');seg(rr*.6,L-2,rr*.80,rr*.81,'#728894')
        for u in [.34,.55,.78,.97]:seg(L*u-2,L*u+2,rr,rr,'#c3eee6' if u!=.55 else '#f9b35b')
        muzzle=tuple(a+v*L)
        if t<1.50:
            intensity=ease((t-.25)/.95);radius=(3+6*intensity+3*math.sin(19*t)*intensity)*sx
            d.ellipse(box(*muzzle,radius),fill=color);ring(muzzle,radius*1.8,color,2)
            for k in range(3):
                aa=t*31+120*k;rx=w*(.037+.035*(1-q))
                d.arc(box(*pivot,rx),aa,aa+60,fill=color,width=max(1,round(2*sx)))
        if 1.1<t<1.53:
            phase=ease((t-1.1)/.38);tip=tuple(a+v*L+(np.array(pivot)-a-v*L)*phase)
            tail=tuple(a+v*L+(np.array(pivot)-a-v*L)*clamp(phase-.34))
            line(d,[tail,tip],(38,154,248),15*sx);line(d,[tail,tip],(205,251,255),5*sx)
            star(muzzle,16*sx*math.sin(math.pi*clamp((t-1.08)/.47)),color)
        if -.02<age<.39:
            s=clamp((age+.02)/.41);ring(pivot,(.017+.19*s)*w,tuple(int(k*(1-s)) for k in color),5*(1-s))
            star(pivot,(1-s)*w*.105,(145,222,250),9,.47)
    if mode=='whirlpool' and t<2.7:
        # Cast a weighted line, catch the edge, pull it into a spinning drain.
        s=ease((t-.21)/.58);p=tuple(interp(v,k,s) for v,k in zip(hand,pivot));pts=[]
        for n in range(40):
            x=n/39; sag=(1-ease((t-.90)/.45))*.115*h*math.sin(math.pi*x)
            pts.append((interp(hand[0],p[0],x),interp(hand[1],p[1],x)+sag))
        if .21<t<1.86:
            line(d,pts,(31,113,139),6*sx);line(d,pts,(147,246,222),2*sx);ring(p,8*sx,color,3)
        if t>.8:
            for j in range(3):
                ang=np.linspace(0,math.pi*1.72,70)+t*3+j*math.tau/3;rr=np.linspace(7,w*(.075+.11*ease(age/.64)),70)
                colorj=tuple(int(v*max(0,1-max(0,age-.23))) for v in color)
                line(d,list(zip(pivot[0]+rr*np.cos(ang),pivot[1]+rr*.78*np.sin(ang))),colorj,2*sx)
    if mode=='hot_seam' and .28<t<2.75:
        m=landmarks[name];bx,by=m['boots'][0][0]
        foot=(cx-size*360/380/2+bx*size/380,cy-size/2+by*size/380)
        top=ease((t-.39)/1.09)
        cut=[(w*(.38+.12*z+.025*math.sin(13*z)+.017*math.sin(29*z)),z*h) for z in np.linspace(.99,1-.98*top,45)]
        fade=1-ease((age-.05)/.74); cc=tuple(int(fade*v) for v in (255,141,37))
        if fade:
            line(d,cut,cc,8*sx);line(d,cut,tuple(int(fade*v) for v in (255,243,178)),3*sx)
            line(d,[foot,cut[-1]],cc,6*sx);star(cut[-1],18*sx*fade,cc,7,t*2)
        for n in range(22):
            f=(max(0,t-.35)*1.55+n*.083)%1; ang=.18+n*2.4
            x=cut[-1][0]+math.cos(ang)*f*w*.14;y=cut[-1][1]+math.sin(ang)*f*h*.18+f*f*h*.12
            line(d,[(x,y),(x-math.cos(ang)*sx*6,y-math.sin(ang)*sx*5)],tuple(int(v*(1-f)*fade) for v in color),2*sx)
    if mode=='prism' and .08<t<2.66:
        q=ease((t-.08)/.66);a=hand;pr=(interp(a[0],w*.44,q),interp(a[1],h*.43,q))
        r=(12+36*q)*sx;rot=t*.95
        pts=[(pr[0]+r*math.cos(rot+j*math.tau/3),pr[1]+r*math.sin(rot+j*math.tau/3)) for j in range(3)]
        line(d,pts+[pts[0]],(203,212,255),3*sx)
        for pp in pts:line(d,[pp,pr],(77,145,204),2*sx)
        if t>.68:
            for j,col in enumerate([(255,99,76),(96,224,249),(199,139,255)]):
                pull=ease((t-.68-j*.13)/.53)
                end=(interp(pr[0],.04*w,pull),interp(pr[1],(.14+.3*j)*h,pull))
                line(d,[hand,pr],(138,167,245),2*sx);line(d,[pr,end],col,4*sx)
    if mode in ('iris','shutters') and .15<t<1.91:
        engage=ease((t-.15)/.75);dest=(w*.48,h*.46)
        tip=(interp(hand[0],dest[0],engage),interp(hand[1],dest[1],engage))
        line(d,[hand,tip],color,3*sx)
        if mode=='iris':
            for j in range(6):
                theta=j*math.tau/6+ease((t-.5)/.9)*1.45
                r=w*.07
                pp=(dest[0]+r*math.cos(theta),dest[1]+r*math.sin(theta))
                line(d,[dest,pp],color,2*sx);ring(pp,4*sx,color,2)
            ring(dest,w*.076,color,3)
        else:
            for j in range(4):
                y=dest[1]+(j-1.5)*h*.027
                line(d,[(dest[0]-w*.035,y),(dest[0]+w*.035,y)],color,3*sx)
            ring(tip,7*sx,color,2)
    return addlight(stage,fx)

def sound(samples,fs,start,mode):
    """Signature cues for the four gestures. Diffuse tails sit behind the dry dialogue."""
    rng=np.random.default_rng(9283+MODES.index(mode)); n=len(samples)
    def add(at,dur,f0,f1,amp,pan,noise=0.,decay=3.,sweep=False):
        i=round((start+at)*fs);length=min(round(dur*fs),n-i)
        if i<0 or length<3:return
        t=np.arange(length)/fs;u=t/dur; phase=2*math.pi*(f0*t+(f1-f0)*t*t/(2*dur))
        v=np.sin(phase)+.23*np.sin(phase*1.995)
        if noise:
            wind=sosfilt(butter(2,[max(45,min(3000,f0*.6)),min(fs*.43,max(f0*2.5,1900))],fs=fs,btype='band',output='sos'),rng.standard_normal(length))
            v=v*(1-noise)+noise*wind*2
        env=(np.sin(math.pi*np.minimum(1,u*12)/2)**2)*np.minimum(1,(1-u)*15)*(np.sin(np.pi*u)**1.2 if sweep else np.exp(-decay*u))
        sig=amp*env*v
        samples[i:i+length,0]+=sig*math.sqrt((1-pan)/2);samples[i:i+length,1]+=sig*math.sqrt((1+pan)/2)
    if mode=='cannon':
        add(.08,.8,195,670,.12,.3,.32,sweep=True)
        add(1.10,.32,820,185,.36,.26,.37);add(1.47,.85,108,40,.43,-.29,.29)
        for j in range(19):add(1.51+j*.055,rng.uniform(.12,.43),rng.uniform(950,3500),rng.uniform(620,1450),rng.uniform(.035,.083),rng.uniform(-.89,.89),.27,5)
    elif mode=='whirlpool':
        add(.20,.63,270,1220,.19,.38,.50,sweep=True);add(.83,.16,1230,650,.17,-.32)
        add(1.38,1.78,520,71,.30,-.15,.58,sweep=True)
        for j in range(11):add(1.52+j*.127,.15,175+j*19,430+j*42,.087,math.sin(j*1.4)*.67)
    elif mode=='hot_seam':
        add(.31,1.35,128,615,.25,.22,.65,sweep=True);add(1.47,1.28,94,42,.34,-.14,.54)
        for j in range(18):add(.61+j*.086,.07,460+rng.uniform(0,720),150,.09,rng.uniform(-.7,.7),.78,6)
    elif mode=='prism':
        for j,f in enumerate([294,440,659]):add(.35+j*.26,1.20,f,f*1.006,.10,(j-1)*.61,0,4)
        for j,f in enumerate([587,880,1318]):add(1.50+j*.14,1.25,f*1.49,f,.105,(j-1)*.75,.11,4)
    else:
        for j in range(5):add(1.24+j*.10,.28,170+j*55,110,.09,(j-2)*.22,.55)
    add(2.47,.97,240,74,.14,-.32,.65,sweep=True)

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description="Choose a repeatable, shuffled cast of chapter reveals.")
    parser.add_argument('episode',help='A different identifier for each episode')
    parser.add_argument('--chapters',type=int,default=4)
    parser.add_argument('--previous',choices=MODES,help='Last effect in the preceding episode')
    parser.add_argument('--output',type=Path,help='Write the cast here, otherwise print it')
    args=parser.parse_args()
    if args.chapters<1:parser.error('The episode needs at least one chapter.')
    data={'episode':args.episode,'chapters':args.chapters,'casts':cast(args.episode,args.chapters,args.previous)}
    payload=json.dumps(data,indent=2)+'\n'
    if args.output:args.output.write_text(payload)
    else:print(payload,end='')
