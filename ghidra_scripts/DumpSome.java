// Dump decompiled C for functions whose names/addresses match given prefixes.
//@category Export

import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionIterator;

import java.io.FileWriter;
import java.io.Writer;

public class DumpSome extends GhidraScript {
    @Override
    public void run() throws Exception {
        String out = getScriptArgs()[0];
        String[] pats = getScriptArgs().length > 1
                ? getScriptArgs()[1].split(",") : new String[0];
        DecompInterface di = new DecompInterface();
        di.openProgram(currentProgram);
        FunctionIterator it = currentProgram.getFunctionManager().getFunctions(true);
        try (Writer w = new FileWriter(out)) {
            while (it.hasNext()) {
                Function f = it.next();
                String n = f.getName();
                boolean match = false;
                for (String p : pats)
                    if (n.startsWith(p) || n.contains(p)) { match = true; break; }
                if (!match) continue;
                if (f.getBody().getNumAddresses() == 0) continue;
                w.write("\n/* ==== " + n + " @ " + f.getEntryPoint() + " ==== */\n");
                DecompileResults r = di.decompileFunction(f, 90, monitor);
                if (r.decompileCompleted() && r.getDecompiledFunction() != null)
                    w.write(r.getDecompiledFunction().getC());
                else
                    w.write("/* failed: " + r.getErrorMessage() + " */\n");
            }
        }
        di.dispose();
        println("Wrote " + out);
    }
}
