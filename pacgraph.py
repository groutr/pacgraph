#! /usr/bin/env python

import random, math, subprocess, os, cProfile
from optparse import OptionParser
from itertools import *
from collections import deque, defaultdict

# depends contains %CONFLICTS%, 
# %DEPENDS%, %OPTDEPENDS%, %PROVIDES%

# desc contains %URL%, %REPLACES%, %LICENSE%,
# %NAME%, %GROUPS%, %BUILDDATE%, %REASON%, %DESC%,
# %SIZE%, %PACKAGER%, %ARCH%, %INSTALLDATE%, %VERSION%

pj = os.path.join

def l_part(n, c):
    return n.partition(c)[0]

def reduce_by(fn, data, arg_list):
    data = fn(data, arg_list.pop(0))
    if not arg_list:
        return data
    return reduce_by(fn, data, arg_list)

# start ArchLinux specific code
def clean(n):
    return reduce_by(l_part, n.strip(), list('><:='))

def load_info(arch_file):
    info = defaultdict(list)
    mode = None
    for line in (clean(l) for l in arch_file):
        if not line:
            continue
        if line.startswith('%'):
            mode = line
            continue
        info[mode].append(line)
    arch_file.close()
    return info

def strip_info(info):
    keep = ['DEPENDS', 'OPTDEPENDS', 'PROVIDES', 'SIZE', 'ISIZE']
    info = dict((k.strip('%'),v) for k,v in info.items())
    name = info['NAME'][0]
    info = dict((k,v) for k,v in info.items() if k in keep)
    if 'ISIZE' in info:
        info['SIZE'] = info['ISIZE']
    if 'SIZE' in info:
        info['SIZE'] = int(info['SIZE'][0], 10)
    else:
        info['SIZE'] = 0
    return name, info

def load_tree(dirs):
    packages = [p for r in dirs for p,d,f in os.walk(r) if f]
    tree = {}
    for p in packages:
        try:
            info = {}
            arch_file = open(pj(p,'depends'), 'r')
            info.update(load_info(arch_file))
            arch_file = open(pj(p,'desc'), 'r')
            info.update(load_info(arch_file))
            name, info = strip_info(info)
            tree[name] = info
        except:
            print 'Error reading package', p
    return tree

def search_provides(package, tree):
    "use only on load_tree data"
    tree2 = dict((p,tree[p]['PROVIDES']) for p in tree if 'PROVIDES' in tree[p])
    return [p for p in tree2 if package in tree2[p]]

def actually_installed(packages, tree):
    "use only on load_tree data"
    installed = set(packages) & set(tree.keys())
    maybe = set(packages) - installed
    for p in maybe:
        provides = search_provides(p, tree)
        if len(provides) > 1:
            print 'error:', p, 'found in', provides
        if len(provides) == 1:
            installed.add(provides[0])
        # len 0 means not installed optdep
    return list(installed)

def merge_tree(tree):
    "merge provides, depends, optdepends"
    tree2 = {}
    # merge
    for p in tree:
        tp = defaultdict(list, tree[p])
        deps = tp['DEPENDS'] + tp['OPTDEPENDS']
        # remove unused optdeps
        deps = actually_installed(deps, tree)
        tree2[p] = (tree[p]['SIZE'], deps)
    return tree2
# end ArchLinux specific code

def full_deps(package, tree):
    "returns every package in dep tree"
    deps = set()
    to_crawl = deque([package])
    while to_crawl:
        current = to_crawl.popleft()
        if current in deps:
            continue
        deps.add(current)
        current_deps = set(tree[current][1])
        to_crawl.extend(current_deps - deps)
    return list(deps)

def invert_tree(tree):
    "turns depends-on into required-by"
    reqs = dict((p,(tree[p][0], [])) for p in tree)
    for p in tree:
        deps = tree[p][1]
        [reqs[d][1].append(p) for d in deps]
    return reqs

def flatten(list_of_lists):
    return list(chain.from_iterable(list_of_lists))

def rle(m):
    return ((n, len(list(g))) for n,g in groupby(m))

def all_deps(tree):
    return flatten(v[1] for k,v in tree.items())

def single_depends(tree):
    "packages with only one parent"
    dep_count = (rle(sorted(all_deps(tree))))
    return (n for n,l in dep_count if l == 1)

def compress_chains(tree):
    "single depends are absorbed into parent"
    while True:
        singles = single_depends(tree)
        try:
            s = singles.next()
        except StopIteration:
            return tree
        req_by = invert_tree(tree)
        parent = req_by[s][1][0]
        #print 'merge', s, 'into', parent
        new_size = tree[parent][0] + tree[s][0]
        new_deps = tree[parent][1] + tree[s][1]
        new_deps = list(set(new_deps) - set([s]))
        tree[parent] = (new_size, new_deps)
        tree.pop(s)

def sum_sizes(packages, tree):
    return sum(tree[p][0] for p in packages if p in tree)

def shared_size(package, tree):
    "package and all deps"
    return sum_sizes(full_deps(package, tree), tree)

def biggest_packs(tree):
    packs = [(shared_size(p, tree), p) for p in tree]
    return [p for s,p in reversed(sorted(packs))]

def dep_sizes(tree):
    "include deps in size"
    return dict((p, (shared_size(p, tree), tree[p][1])) for p in tree)

def arch_load():
    dirs = ['/var/lib/pacman/local/']
    return compress_chains(merge_tree(load_tree(dirs)))

def arch_repo_load(packages):
    dirs = ['/var/lib/pacman/sync/community/',
            '/var/lib/pacman/sync/core/',
            '/var/lib/pacman/sync/extra/']
    tree = merge_tree(load_tree(dirs))
    if not packages:
        return compress_chains(tree)
    deps = [d for p in packages for d in full_deps(p, tree)]
    tree2 = dict((k,v) for k,v in tree.iteritems() if k in deps)
    return tree2

def toplevel_packs(tree):
    "do this before bidrection, returns set"
    return set(tree.keys()) - set(all_deps(tree))

#print 'worst shared packages:', biggest_packs(tree)[:20]
#print 'most crucial packages:', biggest_packs(invert_tree(tree))[:20]

def bidirection(packs):
    packs2 = invert_tree(packs)
    for name in packs2:
        packs2[name][1].extend(packs[name][1])
        packs2[name] = packs2[name][0], list(set(packs2[name][1]))
    return packs2

def pt_sizes(tree, min_pt=10, max_pt=100):
    "size in bytes -> size in points"
    sizes = [deps[0] for p,deps in tree.iteritems()]
    min_s,max_s = min(sizes), max(sizes)
    for p, deps in tree.iteritems():
        size = deps[0]
        pt = int((max_pt-min_pt)*(size-min_s)/(max_s-min_s) + min_pt)
        tree[p] = (pt, tree[p][1])
    return tree

def prioritized(packs):
    "returns list of names, sorted by priority"
    # first are the most 'central'
    stats = [(len(v[1]), k) for k,v in packs.items()]
    stats = [n for l,n in reversed(sorted(stats))]
    # but slip in anyone who's deps are met early
    stats2 = []
    for n in (n for n in stats if n not in stats2):
        stats2.append(n)
        plotted = set(stats2)
        deps_met = [k for k,v in packs.items() if set(v[1]) <= plotted]
        stats2.extend(set(deps_met) - plotted)
    return stats2

def ran_rad():
    return random.random()*2*math.pi

def bbox(center, dim):
    c,d = center,dim
    x1,x2 = c[0]-d[0]//2, c[0]+d[0]//2
    y1,y2 = c[1]-d[1]//2, c[1]+d[1]//2
    return [x1, y1, x2, y2] 

def common_ranges(r1, r2):
    "returns true if overlap"
    if r1 < r2:
        return r1[1] > r2[0]
    return r2[1] > r1[0]

def in_box(bbox1, bbox2):
    cr = common_ranges
    r1x = bbox1[0::2]
    r1y = bbox1[1::2]
    r2x = bbox2[0::2]
    r2y = bbox2[1::2]
    return cr(r1x, r2x) and cr(r1y, r2y)

def all_bboxes(name, coords, pri=None):
    if pri is None:
        name_list = coords.keys()
    else:
        name_list = pri[:pri.index(name)]
    return [bbox(*coords[n]) for n in name_list]

def normalize(point, origin):
    p2 = point[0]-origin[0], point[1]-origin[1]
    length = (p2[0]**2 + p2[1]**2)**0.5
    return p2[0]/length, p2[1]/length

def link_pull(name, origin_n, packs, coords):
    "average of angles of links"
    origin = coords[origin_n][0]
    norm_ps = lambda ps: [normalize(c, origin) for c in ps if c not in [(0,0), origin]] 
    good_links = packs[name][1]
    bad_links  = packs[origin_n][1]
    g_centers  = norm_ps(coords[l][0] for l in good_links)
    b_centers  = norm_ps(coords[l][0] for l in bad_links)
    b_centers  = [(-x,-y) for x,y in b_centers]
    centers = g_centers + b_centers
    if not centers:  
        # new branch, try to avoid existing branches
        centers = norm_ps(coords[l][0] for l in coords.keys())
        if not centers:
            return (0,0)
        centers = [(-x,-y) for x,y in centers]
    return map(sum, zip(*centers))

def xy2rad(x,y):
    "adds some wiggle so things are less spindly"
    if (x,y) == (0,0):
        return ran_rad()
    wiggle = 0.35  # radians
    wiggle = random.random()*wiggle - wiggle/2.0
    return math.atan2(y,x) + wiggle

def pol2xy(o,a,r):
    return int(o[0]+r*math.cos(a)), int(o[1]+r*math.sin(a))

def pt2dim(name, pt):
    x_scale = 0.65
    y_scale = 1.50
    return int(len(name)*pt*x_scale), int(pt*y_scale)

def empty_coords(packs):
    return dict((k, [(0,0), pt2dim(k,v[0])]) for k,v in packs.items())

def best_origin(name, pri, packs):
    "returns sibling with most links, or root"
    possible = pri[:pri.index(name)]
    possible = [n for n in possible if n in packs[name][1]]
    if not possible:
        return pri[0]  # root package
    return possible[0]

def search(cd, origin, heading, scale, b_list):
    "binary search recursive closure thingy, returns radius"
    def probe(r):
        "returns true if clear"
        cd[0] = pol2xy(origin, heading, r)
        bb1 = bbox(*cd)
        return not any(in_box(bb1, bb2) for bb2 in b_list)
    def search2(step, r):
        if probe(r-step//2):
            if step < 8*scale:
                return r-step//2
            return search2(step//2, r-step//2)
        if probe(r+step//2):
            return search2(step//2, r+step//2)
        return search2(step*2, r+step*2)
    return search2(scale*5, scale*5)

def place(packs):
    "radial placement algo, returns non-overlapping coords"
    coords = empty_coords(packs)
    # coords = {name: [(x_pos,y_pos), (x_size, y_size)], ...}
    pri = prioritized(packs)
    for name in pri[1:]:
        origin_name = best_origin(name, pri, packs)
        print 'placing', name, 'around', origin_name
        origin = coords[origin_name][0]
        heading = xy2rad(*link_pull(name, origin_name, packs, coords))
        scale = len(packs[name][1])+1  # more links need more room
        b_list = all_bboxes(name, coords, pri)
        r = search(coords[name], origin, heading, scale, b_list)
        coords[name][0] = pol2xy(origin, heading, r)
    return coords

def offset_coord(c,d):
    "corrects textbox origin"
    return c[0]-d[0]//2, c[1]  #+d[1]//2

def xml_wrap(tag, inner, **kwargs):
    kw = ' '.join('%s="%s"' % (k, str(v)) for k,v in kwargs.items())
    if inner is None:
        return '<%s %s/>' % (tag, kw)
    return '<%s %s>%s</%s>' % (tag, kw, inner, tag)

def control_point(p1, p2):
    dx = abs(p2[0] - p1[0])
    lower  = (p1,p2)[p1[1]<p2[1]]
    higher = (p2,p1)[p1[1]<p2[1]]
    return (lower[0]+higher[0])//2, lower[1]+dx//2

def quad_spline(p1, p2):
    "boofor DSL in XML"
    c = control_point(p1, p2)
    return 'M%i,%i Q%i,%i %i,%i' % (p1+c+p2)

def svg_text(text, center_dim, size):
    p = offset_coord(*center_dim)
    kw = {'x':p[0], 'y':p[1], 'font-size':size}
    return xml_wrap('text', text, **kw) 

def svg_spline(point1, point2):
    return xml_wrap('path', None, d=quad_spline(point1, point2))

def all_points(coords):
    "slightly incomplete, clips the splines"
    return flatten((bb[:2],bb[2:]) for bb in all_bboxes(None,coords))

def recenter(coords, points):
    "shift everything into quadrant 1"
    min_x,min_y = map(min, zip(*points))
    for name in coords:
        p = coords[name][0]
        coords[name][0] = p[0]-min_x, p[1]-min_y
    return coords

def window_size(points):
    xs,ys = zip(*points)
    return max(xs)-min(xs), max(ys)-min(ys)

def svgify(packs, coords, toplevel, options):
    bottomlevel = set(packs) - toplevel
    all_ps = all_points(coords)
    coords = recenter(coords, all_ps)
    text1 = [svg_text(p, coords[p], packs[p][0]) for p in bottomlevel]
    text2 = [svg_text(p, coords[p], packs[p][0]) for p in toplevel]
    paths = []
    for pack in packs:
        size,links = packs[pack]
        p1 = coords[pack][0]
        paths.extend(svg_spline(p1,coords[l][0]) for l in links if l<pack)
    svg = xml_wrap('g', '\n'.join(paths), style='stroke:%s; stroke-opacity:0.15; fill:none;' % options.link)
    svg += xml_wrap('g', '\n'.join(text1), **{'font-family':'Monospace', 'fill':options.dependency})
    svg += xml_wrap('g', '\n'.join(text2), **{'font-family':'Monospace', 'fill':options.toplevel})
    svg = xml_wrap('svg', svg, **dict(zip(['width','height'],window_size(all_ps))))
    open('pacgraph.svg', 'w').write(svg)


def call(cmd):
    subprocess.call([cmd], shell=True)

def parse():
    parser = OptionParser()
    default_action = 'arch'
    parser.add_option('-b', '--background', dest='background', default='#ffffff')
    parser.add_option('-l', '--link', dest='link', default='#606060')
    parser.add_option('-t', '--top', dest='toplevel', default='#0000ff')
    parser.add_option('-d', '--dep', dest='dependency', default='#6a6aa2')
    parser.add_option('-p', '--point', dest='point_size', type='int', nargs=2, default=(10,100))
    parser.add_option('-s', '--svg', dest='svg_only', action='store_true', default=False)
    parser.add_option('-m', '--mode', dest='mode', default=default_action)
    options, args = parser.parse_args()
    return options, args

def main():
    options, args = parse()
    print 'Loading package info'
    if options.mode == 'arch':
        tree = arch_load()
    if options.mode == 'arch-repo':
        tree = arch_repo_load(args)
    tree = pt_sizes(tree, *options.point_size)
    toplevel = toplevel_packs(tree)
    packs = bidirection(tree)
    print 'Placing all packages'
    coords = place(packs)
    print 'Saving SVG'
    svgify(packs, coords, toplevel, options)
    if options.svg_only:
        return
    print 'Rendering SVG'
    if 'inkscape' in tree:
        call('inkscape -D -b "%s" -e pacgraph.png pacgraph.svg' % options.background)
        return
    if 'svg2png' in tree:
        call('svg2png pacgraph.svg pacgraph.png')
        call('mogrify -background white -layers flatten pacgraph.png')
        return
    if 'imagemagick' in tree:
        call('convert pacgraph.svg pacgraph.png')
        return
    print 'No way to convert SVG to PNG.'
    print 'Inkscape, svg2png or imagemagick would be nice.'

if __name__ == "__main__":
    main()
    #cProfile.run("main()", sort=1)

"""
possible/future command line options

-f  --file        output file name
-a  --add         packages
-c  --chains      retain package chains
-d  --dot         load dot file

line weight? alpha? tree dump/load? arg for distro? system stats?
"""
