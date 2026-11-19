"""Four original Send Radar chapter transitions for two same-sized Pillow RGB frames."""
import math
from PIL import Image, ImageDraw
C=["#73eddc","#98e5b1","#ff965a","#ffca67"]
def clamp(x):return min(1,max(0,x))
def ease(x):
 x=clamp(x);return x*x*(3-2*x)
def boundary(out,prior,u,c,seed):
 # Four different edit mechanisms, animated over original image textures.
 if u>=1:return out
 w,h=out.size;u=ease(u);d=ImageDraw.Draw(out);color=C[c]
 if c==0:
  # Screen fragments leave at staggered velocities.
  for y in range(4):
   for x in range(7):
    x0=x*w//7;x1=(x+1)*w//7;y0=y*h//4;y1=(y+1)*h//4
    phase=clamp((u-((x*9+y*4)%11)*.018)/.82);dx=int((x-3.0)*w*.29*phase**1.4);dy=int((y-1.5)*h*.75*phase**1.5+h*.9*phase**2)
    if phase<.97:
     tile=prior.crop((x0,y0,x1,y1));out.paste(tile,(x0+dx,y0+dy))
     if .04<phase<.38:d.line((x0+dx,y0+dy,x1+dx,y0+dy),fill='#b7f3ee',width=2)
 elif c==1:
  base=int(h*(1-u));d=ImageDraw.Draw(out)
  for x in range(0,w,9):
   y=max(0,min(h,int(base+math.sin(x/w*11+u*7)*h*.055*math.sin(math.pi*u))))
   if y>0:out.paste(prior.crop((x,0,min(x+9,w),y)),(x,0))
   if 0<y<h:d.line((x,y,x+9,y),fill=color,width=3)
  for j in range(9):
   x=int((j+.4)*w/9);yy=base+int(.13*h*math.sin(j));d.line((x,base,x+int(12*math.sin(j+u*9)),yy),fill=color,width=2)
 elif c==2:
  mask=Image.new('L',(w,h),0);md=ImageDraw.Draw(mask);rr=u*math.hypot(w,h)*.81;xx=w*.49;yy=h*.62
  pts=[(xx+rr*(1+.095*math.sin(j*31+seed))*math.cos(j*math.pi/30),yy+rr*(1+.095*math.sin(j*31+seed))*math.sin(j*math.pi/30)) for j in range(61)]
  md.polygon(pts,fill=255);out=Image.composite(out,prior,mask);d=ImageDraw.Draw(out);d.line(pts,fill='#ffc36c',width=3)
  for j in range(11):
   a=j*.571;d.line((xx+rr*math.cos(a),yy+rr*math.sin(a),xx+(rr+28)*math.cos(a+.02),yy+(rr+28)*math.sin(a+.02)),fill='#ec7951',width=2)
 else:
  for j in range(3):
   y0=j*h//3;y1=(j+1)*h//3;edge=int(w*(1-ease(clamp((u-.09*j)/.82))))
   if edge:
    x0=0 if j%2==0 else w-edge;out.paste(prior.crop((x0,y0,x0+edge,y1)),(x0,y0));xline=edge if j%2==0 else x0;d.line((xline,y0,xline,y1),fill=['#ed744b','#ffcf52','#f1a88e'][j],width=6)
 return out
