import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.*;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.*;
import java.io.*;
import java.util.*;

public class DumpFuncs extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        String outPath = args[0];
        PrintWriter out = new PrintWriter(new FileWriter(outPath));

        DecompInterface di = new DecompInterface();
        DecompileOptions opts = new DecompileOptions();
        di.setOptions(opts);
        di.openProgram(currentProgram);

        Listing lst = currentProgram.getListing();
        LinkedHashSet<Function> todo = new LinkedHashSet<>();
        for (int i = 1; i < args.length; i++) {
            Address a = currentProgram.getAddressFactory().getAddress(args[i]);
            Function f = lst.getFunctionContaining(a);
            if (f == null) {
                out.println("// no function contains " + args[i] + " -> disassembling");
                disassemble(a);
                f = lst.getFunctionContaining(a);
                if (f == null) f = createFunction(a, null);
            }
            if (f != null) todo.add(f);
        }
        out.println("// functions: " + todo.size());
        for (Function f : todo) {
            out.println("\n// ======== " + f.getName() + " @ " + f.getEntryPoint()
                        + "  size=" + f.getBody().getNumAddresses() + " ========");
            ReferenceIterator ri = currentProgram.getReferenceManager()
                    .getReferencesTo(f.getEntryPoint());
            StringBuilder callers = new StringBuilder();
            int n = 0;
            while (ri.hasNext() && n < 20) { callers.append(ri.next().getFromAddress()).append(" "); n++; }
            out.println("// callers: " + callers);
            DecompileResults r = di.decompileFunction(f, 120, monitor);
            if (r != null && r.decompileCompleted()) {
                out.println(r.getDecompiledFunction().getC());
            } else {
                out.println("// DECOMPILE FAILED: " + (r == null ? "null" : r.getErrorMessage()));
            }
            out.flush();
        }
        out.close();
        println("wrote " + outPath);
    }
}
