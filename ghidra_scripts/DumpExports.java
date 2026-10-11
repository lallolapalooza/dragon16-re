// Dumps decompiled C for every exported function (and named functions) of the current program.
//@category Export

import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionIterator;
import ghidra.program.model.symbol.SourceType;

import java.io.FileWriter;
import java.io.Writer;

public class DumpExports extends GhidraScript {
    @Override
    public void run() throws Exception {
        String out = getScriptArgs()[0];
        DecompInterface di = new DecompInterface();
        di.openProgram(currentProgram);
        FunctionIterator it = currentProgram.getFunctionManager().getFunctions(true);
        try (Writer w = new FileWriter(out)) {
            while (it.hasNext()) {
                Function f = it.next();
                boolean isExport = f.getSymbol() != null
                        && f.getSymbol().getSource() == SourceType.IMPORTED;
                boolean hasBody = f.getBody().getNumAddresses() > 0;
                if (!isExport && !hasBody)
                    continue;
                if (!isExport && getScriptArgs().length > 1 && "exports".equals(getScriptArgs()[1]))
                    continue;
                w.write("\n/* ==== " + f.getName() + " @ " + f.getEntryPoint() + " export=" + isExport + " ==== */\n");
                if (!hasBody) { w.write("/* no body (external/thunk) */\n"); continue; }
                DecompileResults r = di.decompileFunction(f, 60, monitor);
                if (r.decompileCompleted() && r.getDecompiledFunction() != null)
                    w.write(r.getDecompiledFunction().getC());
                else
                    w.write("/* decompile failed: " + r.getErrorMessage() + " */\n");
            }
        }
        di.dispose();
        println("Wrote " + out);
    }
}
