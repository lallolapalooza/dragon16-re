// Dump decompiled C for functions referencing a string needle (ascii or utf-16le).
//@category Export

import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.address.AddressIterator;
import ghidra.program.model.listing.Function;
import ghidra.program.model.mem.MemoryAccessException;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.ReferenceManager;

import java.io.FileWriter;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.LinkedHashSet;

public class DumpXref extends GhidraScript {
    @Override
    public void run() throws Exception {
        String out = getScriptArgs()[0];
        String needle = getScriptArgs()[1];
        boolean wide = getScriptArgs().length > 2 && "wide".equals(getScriptArgs()[2]);
        ReferenceManager rm = currentProgram.getReferenceManager();
        DecompInterface di = new DecompInterface();
        di.openProgram(currentProgram);
        LinkedHashSet<Function> hits = new LinkedHashSet<>();

        byte[] nb = needle.getBytes(StandardCharsets.ISO_8859_1);
        byte[] needleBytes = nb;
        if (wide) {
            byte[] w = new byte[nb.length * 2];
            for (int i = 0; i < nb.length; i++) { w[i * 2] = nb[i]; w[i * 2 + 1] = 0; }
            needleBytes = w;
        }

        try (Writer w = new FileWriter(out)) {
            int found = 0;
            for (ghidra.program.model.mem.MemoryBlock blk : currentProgram.getMemory().getBlocks()) {
                if (!blk.isInitialized()) continue;
                byte[] data = new byte[(int) blk.getSize()];
                try { blk.getBytes(blk.getStart(), data); }
                catch (MemoryAccessException e) { continue; }
                for (int off = 0; off + needleBytes.length <= data.length && found < 40; off++) {
                    if (!Arrays.equals(data, off, off + needleBytes.length, needleBytes, 0, needleBytes.length)) continue;
                    Address addr = blk.getStart().add(off);
                    found++;
                for (Reference r : rm.getReferencesTo(addr)) {
                    Function f = currentProgram.getFunctionManager()
                            .getFunctionContaining(r.getFromAddress());
                    if (f == null || !hits.add(f)) continue;
                    w.write("\n/* ==== " + f.getName() + " @ " + f.getEntryPoint()
                            + " (xref to '" + needle + "') ==== */\n");
                    DecompileResults dr = di.decompileFunction(f, 120, monitor);
                    if (dr.decompileCompleted() && dr.getDecompiledFunction() != null)
                        w.write(dr.getDecompiledFunction().getC());
                    else
                        w.write("/* failed: " + dr.getErrorMessage() + " */\n");
                }
                }
            }
            w.write("\n/* string hits: " + found + ", functions: " + hits.size() + " */\n");
        }
        di.dispose();
        println("Wrote " + out);
    }
}
