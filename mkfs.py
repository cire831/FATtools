# -*- coding: mbcs -*-
import utils, struct, disk, os, sys, pprint
from FAT import *

""" FROM https://support.microsoft.com/en-us/kb/140365

Default cluster sizes for FAT32
The following table describes the default cluster sizes for FAT32.
Volume size	    Windows NT 3.51	    Windows NT 4.0	    Windows 2000+
7 MB�16MB 	    Not supported 	    Not supported	    Not supported
16 MB�32 MB 	512 bytes	        512 bytes	        Not supported
32 MB�64 MB 	512 bytes	        512 bytes	        512 bytes
64 MB�128 MB 	1 KB	            1 KB	            1 KB
128 MB�256 MB	2 KB	            2 KB	            2 KB
256 MB�8GB	    4 KB	            4 KB	            4 KB
8GB�16GB 	    8 KB	            8 KB	            8 KB
16GB�32GB 	    16 KB	            16 KB	            16 KB
32GB�2TB 	    32 KB	            Not supported 	    Not supported
> 2TB	        Not supported 	    Not supported	    Not supported """

"""
Default cluster sizes for FAT16
The following table describes the default cluster sizes for FAT16.
Volume size 	Windows NT 3.51	    Windows NT 4.0	    Windows 2000+
7 MB�8 MB 	    Not supported 	    Not supported	    Not supported
8 MB�32 MB 	    512 bytes	        512 bytes	        512 bytes
32 MB�64 MB 	1 KB             	1 KB 	            1 KB
64 MB�128 MB 	2 KB             	2 KB            	2 KB
128 MB�256 MB	4 KB            	4 KB            	4 KB
256 MB�512 MB	8 KB            	8 KB            	8 KB
512 MB�1 GB 	16 KB            	16 KB            	16 KB
1 GB�2 GB 	    32 KB           	32 KB           	32 KB
2 GB�4 GB 	    64 KB	            64 KB           	64 KB
4 GB�8 GB 	    Not supported 	    128 KB*         	Not supported
8 GB�16 GB 	    Not supported 	    256 KB*         	Not supported
> 16 GB	        Not supported 	    Not supported	    Not supported """

nodos_asm_5Ah = '\xB8\xC0\x07\x8E\xD8\xBE\x73\x00\xAC\x08\xC0\x74\x09\xB4\x0E\xBB\x07\x00\xCD\x10\xEB\xF2\xF4\xEB\xFD\x4E\x4F\x20\x44\x4F\x53\x00'



def fat12_mkfs(stream, size, sector=512, params={}):
    "Make a FAT12 File System on stream. Returns 0 for success."
    sectors = size/sector
    
    if sectors < 16 or sectors > 0xFFFFFFFF:
        return 1

    # Minimum is 1 (Boot)
    if 'reserved_size' in params:
        reserved_size = params['reserved_size']*sector
    else:
        reserved_size = 1*sector

    if 'fat_copies' in params:
        fat_copies = params['fat_copies']
    else:
        fat_copies = 2 # default: best setting

    if 'root_entries' in params:
        root_entries = params['root_entries']
    else:
        root_entries = 224

    reserved_size += root_entries*32 # in FAT12/16 this space resides outside the cluster area
    
    allowed = {} # {cluster_size : fsinfo}

    for i in range(9, 17): # cluster sizes 0.5K...64K
        fsinfo = {}
        cluster_size = (2**i)
        clusters = (size - reserved_size) / cluster_size
        fat_size = rdiv((12*(clusters+2))/8, sector) * sector # 12-bit slot
        required_size = cluster_size*clusters + fat_copies*fat_size + reserved_size
        while required_size > size:
            clusters -= 1
            fat_size = rdiv((12*(clusters+2))/8, sector) * sector # 12-bit slot
            required_size = cluster_size*clusters + fat_copies*fat_size + reserved_size
        if clusters > 4085:
            continue
        fsinfo['required_size'] = required_size # space occupied by FS
        fsinfo['reserved_size'] = reserved_size # space reserved before FAT#1
        fsinfo['cluster_size'] = cluster_size
        fsinfo['clusters'] = clusters
        fsinfo['fat_size'] = fat_size # space occupied by a FAT copy
        fsinfo['root_entries'] = root_entries
        allowed[cluster_size] = fsinfo

    if not allowed:
        if clusters > 4085: # switch to FAT16
            print "Too many clusters to apply FAT12: trying FAT16..."
            return fat16_mkfs(stream, size, sector, params)
        print "ERROR: can't apply any FAT12/16/32 format!"
        return 1

    print "* MKFS FAT12 INFO: allowed combinations for cluster size:"
    pprint.pprint(allowed)

    fsinfo = None

    if 'wanted_cluster' in params:
        if params['wanted_cluster'] in allowed:
            fsinfo = allowed[params['wanted_cluster']]
        else:
            print "Specified cluster size of %d is not allowed!" % params['wanted_cluster']
            return -1
    else:
        # MS-inspired selection
        if size <= 2<<20:
            fsinfo = allowed[512] # < 2M
        elif 2<<20 < size <= 4<<20:
            fsinfo = allowed[1024]
        elif 4<<20 < size <= 8<<20:
            fsinfo = allowed[2048]
        elif 8<<20 < size <= 16<<20:
            fsinfo = allowed[4096]
        elif 16<<20 < size <= 32<<20:
            fsinfo = allowed[8192] # 16M-32M
        elif 32<<20 < size <= 64<<20:
            fsinfo = allowed[16384]
        elif 64<<20 < size <= 128<<20:
            fsinfo = allowed[32768]
        else:
            fsinfo = allowed[65536]

    boot = boot_fat16()
    boot.chJumpInstruction = '\xEB\x58\x90' # JMP opcode is mandatory, or CHKDSK won't recognize filesystem!
    boot._buf[0x5A:0x5A+len(nodos_asm_5Ah)] = nodos_asm_5Ah # insert assembled boot code
    boot.chOemID = '%-8s' % 'NODOS'
    boot.wBytesPerSector = sector
    boot.wSectorsCount = 1
    boot.dwHiddenSectors = 0
    boot.uchSectorsPerCluster = fsinfo['cluster_size']/sector
    boot.uchFATCopies = fat_copies
    boot.wMaxRootEntries = fsinfo['root_entries'] # not used in FAT32 (fixed root)
    boot.uchMediaDescriptor = 0xF0 # floppy
    if sectors < 65536: # Is it right?
        boot.wTotalSectors = sectors
    else:
        boot.dwTotalLogicalSectors = sectors
    boot.wSectorsPerFAT = fsinfo['fat_size']/sector
    boot.dwVolumeID = FATDirentry.GetDosDateTime(1)
    boot.sVolumeLabel = '%-11s' % 'NO NAME'
    boot.sFSType = '%-8s' % 'FAT12'
    boot.chPhysDriveNumber = 0
    boot.uchSignature = 0x29
    boot.wBootSignature = 0xAA55
    boot.wSectorsPerTrack = 18
    boot.wHeads = 2

    boot.pack()
    #~ print boot
    #~ print 'FAT, root, cluster #2 offsets', hex(boot.fat()), hex(boot.fat(1)), hex(boot.root()), hex(boot.dataoffs)

    stream.seek(0)
    # Write boot sector
    stream.write(boot.pack())
    # Blank FAT1&2 area
    stream.seek(boot.fat())
    blank = bytearray(boot.wBytesPerSector)
    for i in xrange(boot.wSectorsPerFAT*2):
        stream.write(blank)
    # Initializes FAT1...
    clus_0_2 = '\xF0\xFF\xFF'
    stream.seek(boot.wSectorsCount*boot.wBytesPerSector)
    stream.write(clus_0_2)
    # ...and FAT2
    if boot.uchFATCopies == 2:
        stream.seek(boot.fat(1))
        stream.write(clus_0_2)

    # Blank root at fixed offset
    stream.seek(boot.root())
    stream.write(bytearray(boot.wMaxRootEntries*32))
    
    print "\nSTATUS: successfully applied FAT12 with following parameters:"
    pprint.pprint(fsinfo)

    fat = FAT(stream, boot.fatoffs, boot.clusters(), bitsize=12)

    print "\nNew file system is FAT12, %d clusters of %.1fK, root @%Xh, cluster #2 @%Xh" % (boot.clusters(),boot.cluster/1024.0,boot.root(),boot.dataoffs)
    print fat

    return 0



def fat16_mkfs(stream, size, sector=512, params={}):
    "Make a FAT16 File System on stream. Returns 0 for success."
    sectors = size/sector
    
    if sectors < 16 or sectors > 0xFFFFFFFF:
        return 1

    # Minimum is 1 (Boot)
    if 'reserved_size' in params:
        reserved_size = params['reserved_size']*sector
    else:
        reserved_size = 8*sector # fixed or variable?

    if 'fat_copies' in params:
        fat_copies = params['fat_copies']
    else:
        fat_copies = 2 # default: best setting

    if 'root_entries' in params:
        root_entries = params['root_entries']
    else:
        root_entries = 512

    reserved_size += root_entries*32 # in FAT12/16 this space resides outside the cluster area

    allowed = {} # {cluster_size : fsinfo}

    for i in range(9, 17): # cluster sizes 0.5K...64K
        fsinfo = {}
        cluster_size = (2**i)
        clusters = (size - reserved_size) / cluster_size
        fat_size = rdiv(2*(clusters+2), sector) * sector
        required_size = cluster_size*clusters + fat_copies*fat_size + reserved_size
        while required_size > size:
            clusters -= 1
            fat_size = rdiv(2*(clusters+2), sector) * sector
            required_size = cluster_size*clusters + fat_copies*fat_size + reserved_size
        # Should switch to FAT12?
        if clusters < 4086 or clusters > 65525: # MS imposed limits
            continue
        fsinfo['required_size'] = required_size # space occupied by FS
        fsinfo['reserved_size'] = reserved_size # space reserved before FAT#1
        fsinfo['cluster_size'] = cluster_size
        fsinfo['clusters'] = clusters
        fsinfo['fat_size'] = fat_size # space occupied by a FAT copy
        fsinfo['root_entries'] = root_entries
        allowed[cluster_size] = fsinfo

    if not allowed:
        if clusters > 65525: # switch to FAT32
            print "Too many clusters to apply FAT16: trying FAT32..."
            return fat32_mkfs(stream, size, sector, params)
        if clusters < 4086: # switch to FAT12
            print "Too few clusters to apply FAT16: trying FAT12..."
            return fat12_mkfs(stream, size, sector, params)
        return 1

    print "* MKFS FAT16 INFO: allowed combinations for cluster size:"
    pprint.pprint(allowed)

    fsinfo = None

    if 'wanted_cluster' in params:
        if params['wanted_cluster'] in allowed:
            fsinfo = allowed[params['wanted_cluster']]
        else:
            print "Specified cluster size of %d is not allowed!" % params['wanted_cluster']
            return -1
    else:
        # MS-inspired selection
        if size <= 32<<20:
            fsinfo = allowed[512] # < 32M
        elif 32<<20 < size <= 64<<20:
            fsinfo = allowed[1024]
        elif 64<<20 < size <= 128<<20:
            fsinfo = allowed[2048]
        elif 128<<20 < size <= 256<<20:
            fsinfo = allowed[4096]
        elif 256<<20 < size <= 512<<20:
            fsinfo = allowed[8192] # 256M-512M
        elif 512<<20 < size <= 1<<30:
            fsinfo = allowed[16384]
        elif 1<<30 < size <= 2<<30:
            fsinfo = allowed[32768]
        else:
            fsinfo = allowed[65536]

    boot = boot_fat16()
    boot.chJumpInstruction = '\xEB\x58\x90' # JMP opcode is mandatory, or CHKDSK won't recognize filesystem!
    boot._buf[0x5A:0x5A+len(nodos_asm_5Ah)] = nodos_asm_5Ah # insert assembled boot code
    boot.chOemID = '%-8s' % 'NODOS'
    boot.wBytesPerSector = sector
    boot.wSectorsCount = (reserved_size - fsinfo['root_entries']*32)/sector
    boot.dwHiddenSectors = 1
    boot.uchSectorsPerCluster = fsinfo['cluster_size']/sector
    boot.uchFATCopies = fat_copies
    boot.wMaxRootEntries = fsinfo['root_entries'] # not used in FAT32 (fixed root)
    boot.uchMediaDescriptor = 0xF8
    if sectors < 65536: # Is it right?
        boot.wTotalSectors = sectors
    else:
        boot.dwTotalLogicalSectors = sectors
    boot.wSectorsPerFAT = fsinfo['fat_size']/sector
    boot.dwVolumeID = FATDirentry.GetDosDateTime(1)
    boot.sVolumeLabel = '%-11s' % 'NO NAME'
    boot.sFSType = '%-8s' % 'FAT16'
    boot.chPhysDriveNumber = 0x80
    boot.uchSignature = 0x29
    boot.wBootSignature = 0xAA55
    boot.wSectorsPerTrack = 63 # not used w/o disk geometry!
    boot.wHeads = 16 # not used

    boot.pack()
    #~ print boot
    #~ print 'FAT, root, cluster #2 offsets', hex(boot.fat()), hex(boot.fat(1)), hex(boot.root()), hex(boot.dataoffs)

    stream.seek(0)
    # Write boot sector
    stream.write(boot.pack())
    # Blank FAT1&2 area
    stream.seek(boot.fat())
    blank = bytearray(boot.wBytesPerSector)
    for i in xrange(boot.wSectorsPerFAT*2):
        stream.write(blank)
    # Initializes FAT1...
    clus_0_2 = '\xF8\xFF\xFF\xFF'
    stream.seek(boot.wSectorsCount*boot.wBytesPerSector)
    stream.write(clus_0_2)
    # ...and FAT2
    if boot.uchFATCopies == 2:
        stream.seek(boot.fat(1))
        stream.write(clus_0_2)

    # Blank root at fixed offset
    stream.seek(boot.root())
    stream.write(bytearray(boot.wMaxRootEntries*32))
    
    print "\nSTATUS: successfully applied FAT16 with following parameters:"
    pprint.pprint(fsinfo)

    fat = FAT(stream, boot.fatoffs, boot.clusters(), bitsize=16)

    print "\nNew file system is FAT16, %d clusters of %.1fK, root @%Xh, cluster #2 @%Xh" % (boot.clusters(),boot.cluster/1024.0,boot.root(),boot.dataoffs)
    print fat

    return 0



def fat32_mkfs(stream, size, sector=512, params={}):
    "Make a FAT32 File System on stream. Returns 0 for success, required additional clusters in case of failure."

#~ Windows CHKDSK wants at least 65526 clusters (512 bytes min).
#~ In fact, we can successfully apply FAT32 with less than 65526 clusters to
#~ a small drive (i.e., 32M with 1K/4K cluster) and Windows 10 will read and
#~ write it: but CHKDSK WON'T WORK!
#~ 4177918 (FAT32 limit where exFAT available)
#~ 2^16 - 11 = 65525 (FAT16)
#~ 2^12 - 11 = 4085 (FAT12)
#~ Also, we can successfully apply FAT16 to a 1.44M floppy (2855 clusters): but,
#~ again, we'll waste FAT space and, more important, CHKDSK won't recognize it!

    sectors = size/sector
    
    if sectors > 0xFFFFFFFF: # switch to exFAT where available
        return -1

    # reserved_size auto adjusted according to unallocable space
    # Minimum is 2 (Boot & FSInfo)
    if 'reserved_size' in params:
        reserved_size = params['reserved_size']*sector
    else:
        reserved_size = 32*sector # fixed or variable?

    if 'fat_copies' in params:
        fat_copies = params['fat_copies']
    else:
        fat_copies = 2 # default: best setting

    allowed = {} # {cluster_size : fsinfo}

    for i in range(9, 17): # cluster sizes 0.5K...64K
        fsinfo = {}
        cluster_size = (2**i)
        clusters = (size - reserved_size) / cluster_size
        fat_size = rdiv(4*(clusters+2), sector) * sector
        required_size = cluster_size*clusters + fat_copies*fat_size + reserved_size
        while required_size > size:
            clusters -= 1
            fat_size = rdiv(4*(clusters+2), sector) * sector
            required_size = cluster_size*clusters + fat_copies*fat_size + reserved_size
        if clusters < 65526 or clusters > 0x0FFFFFF6: # MS imposed limits
            continue
        fsinfo['required_size'] = required_size # space occupied by FS
        fsinfo['reserved_size'] = reserved_size # space reserved before FAT#1
        fsinfo['cluster_size'] = cluster_size
        fsinfo['clusters'] = clusters
        fsinfo['fat_size'] = fat_size # space occupied by a FAT copy
        allowed[cluster_size] = fsinfo

    if not allowed:
        if clusters < 65526:
            print "Too few clusters to apply FAT32: trying with FAT16..."
            return fat16_mkfs(stream, size, sector, params)
        else:
            print "Too many clusters to apply FAT32: aborting."
            return -1

    print "* MKFS FAT32 INFO: allowed combinations for cluster size:"
    pprint.pprint(allowed)

    fsinfo = None

    if 'wanted_cluster' in params:
        if params['wanted_cluster'] in allowed:
            fsinfo = allowed[params['wanted_cluster']]
        else:
            print "Specified cluster size of %d is not allowed for FAT32: retrying with FAT16..." % params['wanted_cluster']
            return fat16_mkfs(stream, size, sector, params)
    else:
        # MS-inspired selection
        if size <= 64<<20:
            fsinfo = allowed[512] # < 64M
        elif 64<<20 < size <= 128<<20:
            fsinfo = allowed[1024]
        elif 128<<20 < size <= 256<<20:
            fsinfo = allowed[2048]
        elif 256<<20 < size <= 8<<30:
            fsinfo = allowed[4096] # 256M-8G
        elif 8<<30 < size <= 16<<30:
            fsinfo = allowed[8192]
        elif 16<<30 < size <= 32<<30:
            fsinfo = allowed[16384]
        elif 32<<30 < size <= 2048<<30:
            fsinfo = allowed[32768]
        # Windows 10 supports 128K and 256K, too!
        else:
            fsinfo = allowed[65536]

    boot = boot_fat32()
    boot.chJumpInstruction = '\xEB\x58\x90' # JMP opcode is mandatory, or CHKDSK won't recognize filesystem!
    boot._buf[0x5A:0x5A+len(nodos_asm_5Ah)] = nodos_asm_5Ah # insert assembled boot code
    boot.chOemID = '%-8s' % 'NODOS'
    boot.wBytesPerSector = sector
    boot.wSectorsCount = reserved_size/sector
    boot.wHiddenSectors = 1
    boot.uchSectorsPerCluster = fsinfo['cluster_size']/sector
    boot.uchFATCopies = fat_copies
    boot.uchMediaDescriptor = 0xF8
    boot.dwTotalLogicalSectors = sectors
    boot.dwSectorsPerFAT = fsinfo['fat_size']/sector
    boot.dwRootCluster = 2
    boot.wFSISector = 1
    if 'backup_sectors' in params:
        boot.wBootCopySector = params['backup_sectors'] # typically 6
    else:
        boot.wBootCopySector = 0
    boot.dwVolumeID = FATDirentry.GetDosDateTime(1)
    boot.sVolumeLabel = '%-11s' % 'NO NAME'
    boot.sFSType = '%-8s' % 'FAT32'
    boot.chPhysDriveNumber = 0x80
    boot.chExtBootSignature = 0x29
    boot.wBootSignature = 0xAA55
    boot.wSectorsPerTrack = 63 # not used w/o disk geometry!
    boot.wHeads = 16 # not used

    fsi = fat32_fsinfo(offset=sector)
    fsi.sSignature1 = 'RRaA'
    fsi.sSignature2 = 'rrAa'
    fsi.dwFreeClusters = fsinfo['clusters'] - 1 # root is #2
    fsi.dwNextFreeCluster = 3 #2 is root
    fsi.wBootSignature = 0xAA55

    stream.seek(0)
    # Write boot & FSI sectors
    stream.write(boot.pack())
    stream.write(fsi.pack())
    if boot.wBootCopySector:
        # Write their backup copies
        stream.seek(boot.wBootCopySector*boot.wBytesPerSector)
        stream.write(boot.pack())
        stream.write(fsi.pack())
    # Blank FAT1&2 area
    stream.seek(boot.fat())
    blank = bytearray(boot.wBytesPerSector)
    for i in xrange(boot.dwSectorsPerFAT*2):
        stream.write(blank)
    # Initializes FAT1...
    clus_0_2 = '\xF8\xFF\xFF\x0F\xFF\xFF\xFF\xFF\xF8\xFF\xFF\x0F'
    stream.seek(boot.wSectorsCount*boot.wBytesPerSector)
    stream.write(clus_0_2)
    # ...and FAT2
    if boot.uchFATCopies == 2:
        stream.seek(boot.fat(1))
        stream.write(clus_0_2)

    # Blank root at cluster #2
    stream.seek(boot.root())
    stream.write(bytearray(boot.cluster))
    
    print "\nSTATUS: successfully applied FAT32 with following parameters:"
    pprint.pprint(fsinfo)

    fat = FAT(stream, boot.fatoffs, boot.clusters(), bitsize=32)

    print "\nNew file system is:\nFAT32, %d clusters of %.1fK, root @%Xh, cluster #2 @%Xh" % (boot.clusters(),boot.cluster/1024.0,boot.root(),boot.dataoffs)
    print fat

    return 0



if __name__ == '__main__':
    if len(sys.argv) < 2:
        print "mkfs error: you must specify a target volume to apply a FAT file system!"
        sys.exit(1)

    if os.name == 'nt' and len(sys.argv[1])==2 and sys.argv[1][1]==':':
        disk_name = '\\\\.\\'+sys.argv[1]
    else:
        disk_name = sys.argv[1]
    dsk = disk.disk(disk_name, 'r+b')

    params = {'reserved_size':32, 'backup_sectors':6}
    if len(sys.argv)==3:
        params['wanted_cluster'] = int(sys.argv[2])

    fat32_mkfs(dsk, dsk.size, params=params)
