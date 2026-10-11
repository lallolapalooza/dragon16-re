//@category Export
import ghidra.app.decompiler.*;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.Function;
import ghidra.program.model.symbol.Reference;
import java.io.*;
import java.util.*;

public class DumpCallers extends GhidraScript {
    @Override
    public void run() throws Exception {
        String out = getScriptArgs()[0];
        String target = getScriptArgs()[1];  // function name
        Function tf = null;
        for (Function f : currentProgram.getFunctionManager().getFunctions(true))
            if (f.getName().equals(target)) { tf = f; break; }
        DecompInterface di = new DecompInterface();
        di.openProgram(currentProgram);
        try (Writer w = new FileWriter(out)) {
            if (tf == null) { w.write("target not found\n"); return; }
            w.write("target " + tf.getName() + " @ " + tf.getEntryPoint() + "\n");
            for (Reference r : currentProgram.getReferenceManager().getReferencesTo(tf.getEntryPoint())) {
                Function c = currentProgram.getFunctionManager().getFunctionContaining(r.getFromAddress());
                if (c == null) continue;
                w.write("\n/* ==== caller " + c.getName() + " @ " + c.getEntryPoint() + " ==== */\n");
                DecompileResults dr = di.decompileFunction(c, 120, monitor);
                if (dr.decompileCompleted() && dr.getDecompiledFunction() != null)
                    w.write(dr.getDecompiledFunction().getC());
            }
        }
        di.dispose();
        println("Wrote " + out);
    }
}
