#!/usr/bin/env python

import sys, os, re, argparse, subprocess, csv, inspect, multiprocessing, shutil
from Bio import SeqIO
currentdir = os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))
parentdir = os.path.dirname(currentdir)
sys.path.insert(0,parentdir)
import lib.amptklib as amptklib
from natsort import natsorted

class MyFormatter(argparse.ArgumentDefaultsHelpFormatter):
    def __init__(self,prog):
        super(MyFormatter,self).__init__(prog,max_help_position=48)

class col:
    GRN = '\033[92m'
    END = '\033[0m'
    WARN = '\033[93m'

def restricted_float(x):
    x = float(x)
    if x < 0.0 or x > 1.0:
        raise argparse.ArgumentTypeError("%r not in range [0.0, 1.0]"%(x,))
    return x

parser=argparse.ArgumentParser(prog='amptk-assign_taxonomy.py', usage="%(prog)s [options] -f <FASTA File>",
    description='''assign taxonomy to OTUs''',
    epilog="""Written by Jon Palmer (2015) nextgenusfs@gmail.com""",
    formatter_class=MyFormatter)

parser.add_argument('-i', '--input', dest="otu_table", help='Append Taxonomy to OTU table')
parser.add_argument('-f','--fasta', required=True, help='FASTA input')
parser.add_argument('-o','--out', help='Output file (FASTA)')
parser.add_argument('-m','--mapping_file', help='Mapping file: QIIME format can have extra meta data columns')
parser.add_argument('--method', default='hybrid',choices=['utax', 'usearch', 'sintax', 'hybrid', 'rdp', 'blast'], help='Taxonomy method')
parser.add_argument('-d','--db', help='Pre-installed Databases: [ITS,ITS1,ITS2,16S,LSU,COI]')
parser.add_argument('-t','--taxonomy', help='Incorporate taxonomy calculated elsewhere, 2 column file')
parser.add_argument('--fasta_db', help='Alternative database of fasta sequences')
parser.add_argument('--add2db', help='Custom FASTA database to add to DB on the fly')
parser.add_argument('--utax_db', help='UTAX Reference Database')
parser.add_argument('--utax_cutoff', default=0.8, type=restricted_float, help='UTAX confidence value threshold.')
parser.add_argument('--usearch_db', help='USEARCH Reference Database')
parser.add_argument('--usearch_cutoff', default=0.7, type=restricted_float, help='USEARCH percent ID threshold.')
parser.add_argument('-r', '--rdp', dest='rdp', default='/Users/jon/scripts/rdp_classifier_2.10.1/dist/classifier.jar', help='Path to RDP Classifier')
parser.add_argument('--rdp_db', dest='rdp_tax', default='fungalits_unite', choices=['16srrna', 'fungallsu', 'fungalits_warcup', 'fungalits_unite'], help='Training set for RDP Classifier')
parser.add_argument('--rdp_cutoff', default=0.8, type=restricted_float, help='RDP confidence value threshold')
parser.add_argument('--local_blast', help='Path to local Blast DB')
parser.add_argument('-u','--usearch', dest="usearch", default='usearch9', help='USEARCH8 EXE')
parser.add_argument('--tax_filter', help='Retain only OTUs with match in OTU table')
parser.add_argument('--sintax_cutoff', default=0.8, type=restricted_float, help='SINTAX threshold.')
parser.add_argument('--debug', action='store_true', help='Remove Intermediate Files')
args=parser.parse_args()

if not args.out:
    #get base name of files
    if 'filtered' in args.fasta:
        base = args.fasta.split(".filtered.otus.fa")[0]
    else:
        base = args.fasta.split('.fa')[0]
else:
    base = args.out
    
#remove logfile if exists
log_name = base + '.amptk-taxonomy.log'
if os.path.isfile(log_name):
    os.remove(log_name)

amptklib.setupLogging(log_name)
FNULL = open(os.devnull, 'w')
cmd_args = " ".join(sys.argv)+'\n'
amptklib.log.debug(cmd_args)
print "-------------------------------------------------------"

#initialize script, log system info and usearch version
amptklib.SystemInfo()
#Do a version check
usearch = args.usearch
amptklib.versionDependencyChecks(usearch)

#Setup DB locations and names, etc
DBdir = os.path.join(parentdir, 'DB')
DataBase = { 'ITS1': (os.path.join(DBdir,'ITS.udb'), os.path.join(DBdir, 'ITS1_UTAX.udb'), os.path.join(DBdir, 'ITS.extracted.fa')), 'ITS2': (os.path.join(DBdir,'ITS.udb'), os.path.join(DBdir, 'ITS2_UTAX.udb'), os.path.join(DBdir, 'ITS.extracted.fa')), 'ITS': (os.path.join(DBdir,'ITS.udb'), os.path.join(DBdir, 'ITS_UTAX.udb'), os.path.join(DBdir, 'ITS.extracted.fa')), '16S': (os.path.join(DBdir, '16S.udb'), os.path.join(DBdir, '16S.udb'), os.path.join(DBdir, '16S.extracted.fa')), 'LSU': (os.path.join(DBdir, 'LSU.udb'), os.path.join(DBdir, 'LSU_UTAX.udb'), os.path.join(DBdir, 'LSU.extracted.fa')), 'COI': (os.path.join(DBdir,'COI.udb'), os.path.join(DBdir, 'COI_UTAX.udb'), os.path.join(DBdir, 'COI.extracted.fa'))}

#get DB names up front
if args.db in DataBase:
    utax_db = DataBase.get(args.db)[1]
    usearch_db = DataBase.get(args.db)[0]
    sintax_db = DataBase.get(args.db)[2]
    if not utax_db:
        utax_db = args.utax_db
    if not usearch_db:
        usearch_db = args.usearch_db
else:
    utax_db = args.utax_db
    usearch_db = args.usearch_db

if args.method in ['hybrid', 'usearch', 'utax']:
    if not utax_db and not usearch_db and not args.fasta_db:
        amptklib.log.error("You have not selected a database, need either --db, --utax_db, --usearch_db, or --fasta_db")
        sys.exit(1)

custom_db = None
if args.add2db: #means user wants to add sequences to the usearch database on the fly, so we will grab sintax DB here, as not preformatted
    amptklib.log.info("Adding %s to database" % args.add2db)
    custom_db = base + '.custom_database.fa'
    if args.db: #this means that the fasta files are in sintax_db option
        current_db = sintax_db
    elif args.fasta_db:
        current_db = args.fasta_db
    with open(custom_db, 'wb') as outfile:
        with open(current_db, 'rU') as infile:
            shutil.copyfileobj(infile, outfile)
        with open(args.add2db, 'rU') as infile:
            shutil.copyfileobj(infile, outfile)

#Count records
amptklib.log.info("Loading FASTA Records")
total = amptklib.countfasta(args.fasta)
amptklib.log.info('{0:,}'.format(total) + ' OTUs')

#declare output files/variables here
blast_out = base + '.blast.txt'
rdp_out = base + '.rdp.txt'
utax_out = base + '.usearch.txt'
usearch_out = base + '.usearch.txt'
sintax_out = base + '.sintax.txt'

if not args.taxonomy:
    #start with less common uses, i.e. Blast, rdp
    if args.method == 'blast':
        #check if command line blast installed
        if not amptklib.which('blastn'):
            amptklib.log.error("BLASTN not found in your PATH, exiting.")
            sys.exit(1)
    
        #now run blast remotely using NCBI nt database
        outformat = "6 qseqid sseqid pident stitle"
        if args.local_blast:
            #get number of cpus
            cpus = multiprocessing.cpu_count() - 2
            amptklib.log.info("Running local BLAST using db: %s" % args.local_blast)
            cmd = ['blastn', '-num_threads', str(cpus), '-query', args.fasta, '-db', os.path.abspath(args.local_blast), '-max_target_seqs', '1', '-outfmt', outformat, '-out', blast_out]
            amptklib.runSubprocess(cmd, amptklib.log)
        else:
            amptklib.log.info("Running BLASTN using NCBI remote nt database, this may take awhile")
            cmd = ['blastn', '-query', args.fasta, '-db', 'nt', '-remote', '-max_target_seqs', '1', '-outfmt', outformat, '-out', blast_out]
            amptklib.runSubprocess(cmd, amptklib.log)
    
        #load results and reformat
        new = []
        f = csv.reader(open(blast_out), delimiter='\t')
        for col in f:
            query = col[0]
            gbID = col[1].split("|")[3]
            pident = col[2]
            name = col[3]
            tax = gbID + ";" + name + " (" + pident + ")"
            line = [query,tax]
            new.append(line)
        otuDict = dict(new)
    elif args.method == 'rdp':
        #check that classifier is installed
        try:
            rdp_test = subprocess.Popen(['java', '-Xmx2000m', '-jar', args.rdp, 'classify'], stdout=subprocess.PIPE).communicate()[0].rstrip()
        except OSError:
            amptklib.log.error("%s not found in your PATH, exiting." % args.rdp)
            sys.exit(1)
    
        #RDP database
        amptklib.log.info("Using RDP classifier %s training set" % args.rdp_tax)
    
        #run RDP
        cmd = ['java', '-Xmx2000m', '-jar', args.rdp, 'classify', '-g', args.rdp_tax, '-o', rdp_out, '-f', 'fixrank', args.fasta]
        amptklib.runSubprocess(cmd, amptklib.log)
    
        #load in results and put into dictionary
        new = []
        removal = ["unidentified", "Incertae", "uncultured", "incertae"]
        remove_exp = [re.compile(x) for x in removal]
        f = csv.reader(open(rdp_out), delimiter='\t')
        for col in f:
            if float(col[19]) > args.rdp_cutoff:
                tax = "RDP;k:"+col[2]+",p:"+col[5]+",c:"+col[8]+",o:"+col[11]+",f:"+col[14]+",g:"+col[17]
            elif float(col[16]) > args.rdp_cutoff:
                tax = "RDP;k:"+col[2]+",p:"+col[5]+",c:"+col[8]+",o:"+col[11]+",f:"+col[14]
            elif float(col[13]) > args.rdp_cutoff:
                tax = "RDP;k:"+col[2]+",p:"+col[5]+",c:"+col[8]+",o:"+col[11]
            elif float(col[10]) > args.rdp_cutoff:
                tax = "RDP;k:"+col[2]+",p:"+col[5]+",c:"+col[8]
            elif float(col[7]) > args.rdp_cutoff:
                tax = "RDP;k:"+col[2]+",p:"+col[5]
            elif float(col[4]) > args.rdp_cutoff:
                tax = "RDP;k:"+col[2]
            else:
                tax = "RDP;k:unclassified"
            tax_split = tax.split(",")
            tax = [s for s in tax_split if not any(re.search(s) for re in remove_exp)]
            tax = ",".join(tax)
            line = [col[0],tax]
            new.append(line)
        otuDict = dict(new)
    else:
        #check status of USEARCH DB and run
        if args.method in ['hybrid', 'usearch']:
            if args.fasta_db:
                #now run through usearch global
                amptklib.log.info("Global alignment OTUs with usearch_global (VSEARCH)")
                cmd = ['vsearch', '--usearch_global', args.fasta, '--db', os.path.abspath(args.fasta_db), '--userout', usearch_out, '--id', str(args.usearch_cutoff), '--strand', 'both', '--output_no_hits', '--top_hits_only', '--userfields', 'query+target+id', '--notrunclabels']
                amptklib.runSubprocess(cmd, amptklib.log)
            elif custom_db:
                #now run through usearch global
                amptklib.log.info("Global alignment OTUs with usearch_global (VSEARCH) using custom DB")
                cmd = ['vsearch', '--usearch_global', args.fasta, '--db', os.path.abspath(custom_db), '--userout', usearch_out, '--id', str(args.usearch_cutoff), '--strand', 'both', '--output_no_hits', '--top_hits_only', '--userfields', 'query+target+id', '--notrunclabels']
                amptklib.runSubprocess(cmd, amptklib.log)
            else:
                if usearch_db:
                    #run through USEARCH
                    amptklib.log.info("Global alignment OTUs with usearch_global (USEARCH)")
                    cmd = [usearch, '-usearch_global', args.fasta, '-db', usearch_db, '-userout', usearch_out, '-id', str(args.usearch_cutoff), '-strand', 'both', '-output_no_hits', '-top_hit_only', '-userfields', 'query+target+id']
                    amptklib.runSubprocess(cmd, amptklib.log)
                else:
                    amptklib.log.error("USEARCH DB %s not found, skipping" % usearch_db)

        if args.method in ['hybrid', 'utax']:
            if utax_db:
                #now run through UTAX
                utax_out = base + '.utax.txt'
                amptklib.log.info("Classifying OTUs with UTAX (USEARCH)")
                cutoff = str(args.utax_cutoff)
                cmd = [usearch, '-utax', args.fasta, '-db', utax_db, '-utaxout', utax_out, '-utax_cutoff', cutoff, '-strand', 'plus', '-notrunclabels']
                amptklib.runSubprocess(cmd, amptklib.log)
            else:
                amptklib.log.error("UTAX DB %s not found, skipping" % utax_db)
    
        if args.method in ['hybrid', 'sintax']:
            if args.fasta_db: #if you pass fasta file here, over ride any auto detection
                sintax_db = args.fasta_db
            #now run sintax
            amptklib.log.info("Classifying OTUs with SINTAX (USEARCH)")
            cmd = [usearch, '-sintax', args.fasta, '-db', os.path.abspath(sintax_db), '-tabbedout', sintax_out, '-sintax_cutoff', str(args.sintax_cutoff), '-strand', 'both']
            amptklib.runSubprocess(cmd, amptklib.log)

        #now process results, load into dictionary - slightly different depending on which classification was run.
        if args.method == 'hybrid' and os.path.isfile(utax_out) and os.path.isfile(usearch_out) and os.path.isfile(sintax_out): #now run hybrid approach
            #run upgraded method, first load dictionaries with resuls
            utaxDict = amptklib.classifier2dict(utax_out, args.utax_cutoff)
            sintaxDict = amptklib.classifier2dict(sintax_out, args.sintax_cutoff)
            usearchDict = amptklib.usearchglobal2dict(usearch_out)
            otuList = natsorted(list(usearchDict.keys()))
            #first compare classifier results, getting better of the two
            bestClassify = amptklib.bestclassifier(utaxDict, sintaxDict, otuList)
            #now get best taxonomy by comparing to global alignment results
            otuDict = amptklib.bestTaxonomy(usearchDict, bestClassify)
            
        elif args.method == 'utax' and os.path.isfile(utax_out):
            #load results into dictionary for appending to OTU table
            amptklib.log.debug("Loading UTAX results into dictionary")
            with open(utax_out, 'rU') as infile:
                reader = csv.reader(infile, delimiter="\t")
                otuDict = {rows[0]:'UTAX;'+rows[2] for rows in reader}
    
        elif args.method == 'usearch' and os.path.isfile(usearch_out): 
            #load results into dictionary for appending to OTU table
            amptklib.log.debug("Loading Global Alignment results into dictionary")
            otuDict = {}
            usearchDict = amptklib.usearchglobal2dict(usearch_out)
            for k,v in natsorted(usearchDict.items()):
                pident = float(v[0]) * 100
                pident = "{0:.1f}".format(pident)
                ID = v[1]
                tax = ','.join(v[-1])
                fulltax = 'GS|'+pident+'|'+ID+';'+tax
                otuDict[k] = fulltax

        elif args.method == 'sintax' and os.path.isfile(sintax_out):
            #load results into dictionary for appending to OTU table
            amptklib.log.debug("Loading SINTAX results into dictionary")
            with open(sintax_out, 'rU') as infile:
                reader = csv.reader(infile, delimiter="\t")
                otuDict = {rows[0]:'SINTAX;'+rows[3] for rows in reader} 
else:
    #you have supplied a two column taxonomy file, parse and build otuDict
    amptklib.log.debug("Loading custom Taxonomy into dictionary")
    with open(args.taxonomy, 'rU') as infile:
        reader = csv.reader(infile, delimiter="\t")
        otuDict = {rows[0]:rows[1] for rows in reader} 
                
#now format results
if args.otu_table:
    #check if otu_table variable is empty, then load in otu table
    amptklib.log.info("Appending taxonomy to OTU table and OTUs")
    taxTable = base + '.otu_table.taxonomy.txt'
    tmpTable = base + '.otu_table.tmp'

    #append to OTU table
    counts = 0
    with open(taxTable, 'w') as outTable:
        with open(args.otu_table, 'rU') as inTable:
            #guess the delimiter format
            firstline = inTable.readline()
            dialect = amptklib.guess_csv_dialect(firstline)
            inTable.seek(0)
            #parse OTU table
            reader = csv.reader(inTable, dialect)
            for line in reader:
                if line[0].startswith(("#OTU", "OTUId")):
                    line.append('Taxonomy')
                else:
                    tax = otuDict.get(line[0]) or "No Hit"
                    line.append(tax)
                if args.tax_filter and not args.method == 'blast':
                    if line[0].startswith(("#OTU", "OTUId")):
                        join_line = ('\t'.join(str(x) for x in line))
                    else:
                        if args.tax_filter in line[-1]:
                            join_line = ('\t'.join(str(x) for x in line))
                            counts += 1
                        else:
                            continue
                else:
                    join_line = ('\t'.join(str(x) for x in line))
                    counts += 1
                outTable.write("%s\n" % join_line)

    if args.tax_filter:
        if args.method == 'blast':
            amptklib.log.info("Blast is incompatible with --tax_filter, use a different method")
            tmpTable = args.otu_table
        else:
            nonfungal = total - counts
            amptklib.log.info("Found %i OTUs not matching %s, writing %i %s hits to taxonomy OTU table" % (nonfungal, args.tax_filter, counts, args.tax_filter))
            #need to create a filtered table without taxonomy for BIOM output
            with open(tmpTable, 'w') as output:
                with open(taxTable, 'rU') as input:
                    firstline = input.readline()
                    dialect = amptklib.guess_csv_dialect(firstline)
                    input.seek(0)
                    #parse OTU table
                    reader = csv.reader(input, dialect)
                    for line in reader:
                        del line[-1]
                        join_line = '\t'.join(str(x) for x in line)
                        output.write("%s\n" % join_line)
    else:
        tmpTable = args.otu_table

#append to OTUs 
otuTax = base + '.otus.taxonomy.fa'        
with open(otuTax, 'w') as output:
    with open(args.fasta, 'rU') as input:
        SeqRecords = SeqIO.parse(input, 'fasta')
        for rec in SeqRecords:
            tax = otuDict.get(rec.id) or "No hit"
            rec.description = tax
            SeqIO.write(rec, output, 'fasta')

if not args.taxonomy:
    #output final taxonomy in two-column format, followed by the hits for usearch/sintax/utax if hybrid is used.
    taxFinal = base + '.taxonomy.txt'
    with open(taxFinal, 'w') as finaltax:
        if args.method == 'hybrid':
            finaltax.write('#OTUID\ttaxonomy\tUSEARCH\tSINTAX\tUTAX\n')
            for k,v in natsorted(otuDict.items()):
                usearchResult = usearchDict.get(k)[-1]
                if not usearchResult == 'No hit':
                    usearchResult = ','.join(usearchResult)
                finaltax.write('%s\t%s\t%s\t%s\t%s\n' % (k,v, usearchResult, ','.join(sintaxDict.get(k)[-1]), ','.join(utaxDict.get(k)[-1])))
        else:
            finaltax.write('#OTUID\ttaxonomy\n')
            for k,v in natsorted(otuDict.items()):
                finaltax.write('%s\t%s\n' % (k,v))
else:
    taxFinal = args.taxonomy    
#convert taxonomy to qiime format for biom
if not args.method == 'blast':
    qiimeTax = base + '.qiime.taxonomy.txt'
    amptklib.utax2qiime(taxFinal, qiimeTax)
else:
    amptklib.log.error("Blast taxonomy is not compatible with BIOM output, use a different method")

#create OTU phylogeny for downstream processes
amptklib.log.info("Generating phylogenetic tree")
tree_out = base + '.tree.phy'
cmd = [usearch, '-cluster_agg', args.fasta, '-treeout', tree_out]
amptklib.runSubprocess(cmd, amptklib.log)

#print some summary file locations
amptklib.log.info("Taxonomy finished: %s" % taxFinal)
if args.otu_table and not args.method == 'blast':
    amptklib.log.info("Classic OTU table with taxonomy: %s" % taxTable)
    #output final OTU table in Biom v1.0 (i.e. json format if biom installed)
    outBiom = base + '.biom'
    if amptklib.which('biom'):
        amptklib.removefile(outBiom)
        cmd = ['biom', 'convert', '-i', tmpTable, '-o', outBiom+'.tmp', '--table-type', "OTU table", '--to-json']
        amptklib.runSubprocess(cmd, amptklib.log)
        if args.mapping_file:
            cmd = ['biom', 'add-metadata', '-i', outBiom+'.tmp', '-o', outBiom, '--observation-metadata-fp', qiimeTax, '-m', args.mapping_file, '--sc-separated', 'taxonomy', '--output-as-json']
            amptklib.runSubprocess(cmd, amptklib.log)
        else:
            cmd = ['biom', 'add-metadata', '-i', outBiom+'.tmp', '-o', outBiom, '--observation-metadata-fp', qiimeTax, '--sc-separated',  'taxonomy', '--output-as-json']
            amptklib.runSubprocess(cmd, amptklib.log)
        amptklib.removefile(outBiom+'.tmp')
        amptklib.log.info("BIOM OTU table created: %s" % outBiom)
    else:
        amptklib.log.info("biom program not installed, install via `pip install biom-format`")
amptklib.log.info("OTUs with taxonomy: %s" % otuTax)
amptklib.log.info("OTU phylogeny: %s" % tree_out)  

#clean up intermediate files
if not args.debug:
    for i in [utax_out, usearch_out, sintax_out, qiimeTax, base+'.otu_table.tmp']:
        amptklib.removefile(i)   
print "-------------------------------------------------------"
