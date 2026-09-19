#!/usr/bin/env bash
# Benchmark de quantizacao: 3 versoes Python e 2 C, sempre com 4 cores.
# Apenas gera resultados quando executado; nao altera os codigos-fonte.
set -euo pipefail
shopt -s nullglob
export LC_ALL=C

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
INPUT=${1:-"$ROOT/potm2608a.ppm"}
PYTHON_SERIAL_REPETITIONS=1
PYTHON_PARALLEL_REPETITIONS=3
C_REPETITIONS=3
PYTHON=${PYTHON:-python3}
CC=${CC:-gcc}
RUN_PROFILERS=${RUN_PROFILERS:-1}

if [[ ! -f $INPUT ]]; then echo "Entrada inexistente: $INPUT" >&2; exit 1; fi
if [[ $RUN_PROFILERS != 0 && $RUN_PROFILERS != 1 ]]; then echo 'RUN_PROFILERS deve ser 0 ou 1' >&2; exit 1; fi
for program in "$PYTHON" "$CC" awk mktemp /usr/bin/time; do
    command -v "$program" >/dev/null || { echo "Programa ausente: $program" >&2; exit 1; }
done

RESULTS_ROOT="$ROOT/benchmark-results"
mkdir -p -- "$RESULTS_ROOT"
RUN_DIR=$(mktemp -d "$RESULTS_ROOT/run-XXXXXXXX")
mkdir -p -- "$RUN_DIR/bin" "$RUN_DIR/logs" "$RUN_DIR/timev" "$RUN_DIR/profiles"
OUTPUT=${OUTPUT:-/dev/null}
echo "Resultados: $RUN_DIR"
echo "Imagem: $INPUT | cores: 4 | C: $C_REPETITIONS vezes | Python serial: $PYTHON_SERIAL_REPETITIONS vez | Python paralelo: $PYTHON_PARALLEL_REPETITIONS vezes por configuracao"
echo 'Aviso: com a imagem grande, Valgrind/Cachegrind podem demorar muito e consumir muita memoria.'
echo 'A saida da imagem vai para /dev/null por padrao; use OUTPUT=arquivo.ppm para salva-la.'

"$CC" -O2 -g "$ROOT/color_quantization_cedraz/color_quant.c" -o "$RUN_DIR/bin/c_serial"
"$CC" -O2 -g -fopenmp "$ROOT/paralell_C/color_quantization_parallel.c" -o "$RUN_DIR/bin/c_parallel"

printf 'versao,workers,repeticoes,media_s,aceleracao_pct,speedup,eficiencia\n' > "$RUN_DIR/summary.csv"
printf 'versao,workers,repeticao,tempo_s\n' > "$RUN_DIR/times.csv"
declare -A MEANS
declare -a CASE_NAMES CASE_WORKERS CASE_FAMILIES CASE_REPETITIONS

run_case() {
    local name=$1 workers=$2 family=$3
    local log elapsed sum=0 mean i repetitions
    shift 3
    local -a command=("$@")
    if [[ $name == py_serial ]]; then
        repetitions=$PYTHON_SERIAL_REPETITIONS
    elif [[ $family == py ]]; then
        repetitions=$PYTHON_PARALLEL_REPETITIONS
    else
        repetitions=$C_REPETITIONS
    fi
    echo "[$name / $workers] Medindo $repetitions repeticao(oes)..."
    for ((i=1; i<=repetitions; i++)); do
        log="$RUN_DIR/logs/${name}_${workers}_time_${i}.log"
        if ! LC_ALL=C /usr/bin/time -v -o "$RUN_DIR/timev/${name}_${workers}_time_${i}.timev.txt" "${command[@]}" >"$log" 2>&1; then
            echo "Falha em $name ($workers), repeticao $i. Veja $log" >&2
            exit 1
        fi
        elapsed=$(awk '/Tempo de/{print $(NF-1)}' "$log" | tail -n 1)
        if [[ ! $elapsed =~ ^[0-9]+([.][0-9]+)?$ ]]; then
            echo "Tempo nao encontrado em $log" >&2; exit 1
        fi
        printf '%s,%s,%s,%s\n' "$name" "$workers" "$i" "$elapsed" >> "$RUN_DIR/times.csv"
        sum=$(awk -v a="$sum" -v b="$elapsed" 'BEGIN {printf "%.9f", a+b}')
    done
    mean=$(awk -v s="$sum" -v n="$repetitions" 'BEGIN {printf "%.9f", s/n}')
    MEANS["$name:$workers"]=$mean
    CASE_NAMES+=("$name")
    CASE_WORKERS+=("$workers")
    CASE_FAMILIES+=("$family")
    CASE_REPETITIONS+=("$repetitions")
    echo "  media=${mean}s (speedup calculado ao final)"

}

echo '[ETAPA 1/4] Tempos C: serial e OpenMP (1, 2, 4, 8 threads)'
run_case c_serial 1 c "$RUN_DIR/bin/c_serial" "$INPUT" 4 "$OUTPUT"
for workers in 1 2 4 8; do
    OMP_NUM_THREADS=$workers SERIAL_BASELINE_SECONDS=${MEANS["c_serial:1"]} \
        run_case c_parallel "$workers" c "$RUN_DIR/bin/c_parallel" "$INPUT" 4 "$OUTPUT"
done
echo '[ETAPA 2/4] Tempos Python: serial, multiprocessing e multithreading'
run_case py_serial 1 py "$PYTHON" "$ROOT/quant_in_python/color_quant.py" "$INPUT" 4 "$OUTPUT"
for workers in 1 2 4 8; do
    run_case py_multiprocessing "$workers" py "$PYTHON" "$ROOT/quant_in_python/color_quant_multiprocessing.py" "$INPUT" 4 "$OUTPUT" --workers "$workers" --baseline-seconds "${MEANS["py_serial:1"]}"
done
for workers in 1 2 4 8; do
    run_case py_multithreading "$workers" py "$PYTHON" "$ROOT/quant_in_python/color_quant_multithreading.py" "$INPUT" 4 "$OUTPUT" --workers "$workers" --baseline-seconds "${MEANS["py_serial:1"]}"
done

for index in "${!CASE_NAMES[@]}"; do
    name=${CASE_NAMES[index]}
    workers=${CASE_WORKERS[index]}
    family=${CASE_FAMILIES[index]}
    repetitions=${CASE_REPETITIONS[index]}
    mean=${MEANS["$name:$workers"]}
    baseline=${MEANS["${family}_serial:1"]}
    read -r acceleration speedup efficiency < <(
        awk -v b="$baseline" -v m="$mean" -v w="$workers" \
            'BEGIN {if (m>0) printf "%.2f %.4f %.4f\n", (b/m-1)*100, b/m, b/m/w; else print "NA NA NA"}'
    )
    printf '%s,%s,%s,%s,%s,%s,%s\n' "$name" "$workers" "$repetitions" "$mean" "$acceleration" "$speedup" "$efficiency" >> "$RUN_DIR/summary.csv"
done

"$PYTHON" - "$RUN_DIR" <<'PY'
import csv
import statistics
import sys
from pathlib import Path

run = Path(sys.argv[1])
labels = {
    "User time (seconds)": "user_s",
    "System time (seconds)": "system_s",
    "Elapsed (wall clock) time (h:mm:ss or m:ss)": "wall_s",
    "Percent of CPU this job got": "cpu_pct",
    "Maximum resident set size (kbytes)": "max_rss_kb",
    "Major (requiring I/O) page faults": "major_faults",
    "Minor (reclaiming a frame) page faults": "minor_faults",
    "Voluntary context switches": "voluntary_switches",
    "Involuntary context switches": "involuntary_switches",
}

def parse_wall(value):
    total = 0.0
    for part in value.split(":"):
        total = total * 60 + float(part)
    return total

def read_timev(path):
    result = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        for label, key in labels.items():
            prefix = f"\t{label}: "
            if line.startswith(prefix):
                value = line[len(prefix):].strip().rstrip("%")
                result[key] = parse_wall(value) if key == "wall_s" else float(value)
    missing = set(labels.values()) - result.keys()
    if missing:
        raise ValueError(f"{path}: métricas ausentes: {sorted(missing)}")
    return result

def save_csv(path, rows, fields):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

with (run / "times.csv").open(encoding="utf-8", newline="") as stream:
    trials = list(csv.DictReader(stream))

details = []
groups = {}
for trial in trials:
    name = trial["versao"]
    workers = int(trial["workers"])
    repeat = int(trial["repeticao"])
    path = run / "timev" / f"{name}_{workers}_time_{repeat}.timev.txt"
    row = {"versao": name, "workers": workers, "repeticao": repeat,
           "quantizacao_s": float(trial["tempo_s"]), **read_timev(path)}
    details.append(row)
    groups.setdefault((name, workers), []).append(row)
save_csv(run / "metrics.csv", details,
         ["versao", "workers", "repeticao", "quantizacao_s", *labels.values()])

summary = []
for (name, workers), rows in groups.items():
    serial = "c_serial" if name.startswith("c_") else "py_serial"
    baseline = statistics.mean(r["wall_s"] for r in groups[(serial, 1)])
    wall = statistics.mean(r["wall_s"] for r in rows)
    speedup = baseline / wall if wall else 0.0
    summary.append({
        "versao": name, "workers": workers, "repeticoes": len(rows),
        "wall_medio_s": f"{wall:.6f}",
        "user_medio_s": f"{statistics.mean(r['user_s'] for r in rows):.6f}",
        "system_medio_s": f"{statistics.mean(r['system_s'] for r in rows):.6f}",
        "cpu_medio_pct": f"{statistics.mean(r['cpu_pct'] for r in rows):.2f}",
        "rss_max_kb": int(max(r["max_rss_kb"] for r in rows)),
        "major_faults_medio": f"{statistics.mean(r['major_faults'] for r in rows):.2f}",
        "minor_faults_medio": f"{statistics.mean(r['minor_faults'] for r in rows):.2f}",
        "voluntary_switches_medio": f"{statistics.mean(r['voluntary_switches'] for r in rows):.2f}",
        "involuntary_switches_medio": f"{statistics.mean(r['involuntary_switches'] for r in rows):.2f}",
        "speedup_wall": f"{speedup:.4f}",
        "eficiencia_wall": f"{speedup / workers:.4f}",
    })
save_csv(run / "wall_summary.csv", summary, list(summary[0]))

chosen = [("C serial", "c_serial", 1), ("C OpenMP", "c_parallel", 4),
          ("Python serial", "py_serial", 1),
          ("Python threading", "py_multithreading", 4),
          ("Python multiprocessing", "py_multiprocessing", 4)]
lookup = {(r["versao"], r["workers"]): r for r in summary}
lines = ["# Comparação das cinco versões", "",
         "Medidas de processo completo com `/usr/bin/time -v`. Speedup relativo ao serial da mesma linguagem.", "",
         "| Versão | Wall (s) | User (s) | System (s) | CPU % | RSS máx. (KB) | Major/minor faults | Trocas vol./invol. | Speedup | Eficiência |",
         "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
for title, name, workers in chosen:
    r = lookup[(name, workers)]
    lines.append(f"| {title} | {r['wall_medio_s']} | {r['user_medio_s']} | {r['system_medio_s']} | {r['cpu_medio_pct']} | {r['rss_max_kb']} | {r['major_faults_medio']}/{r['minor_faults_medio']} | {r['voluntary_switches_medio']}/{r['involuntary_switches_medio']} | {r['speedup_wall']} | {r['eficiencia_wall']} |")
lines += ["", "As cinco versões executam quantização CPU-bound. Threads Python podem esperar pelo GIL; processos podem esperar por IPC/pickle, não necessariamente por I/O.",
          "No multiprocessing, cada worker tem interpretador e GIL próprios. Compare no cProfile o custo de iniciar processos e de serializar/deserializar blocos (pickle), e compare o wall-clock e RSS com threading.",
          "O RSS de `/usr/bin/time -v` para multiprocessing não é a soma da memória de todos os processos: os dados e o interpretador podem ocupar memória em cada worker.",
          "A Lei de Amdahl é S(N) ≤ 1/((1-p)+p/N); sem estimar p, não há limite teórico numérico confiável.", ""]
(run / "comparacao.md").write_text("\n".join(lines), encoding="utf-8")
PY
if [[ $RUN_PROFILERS == 1 ]]; then
    echo '[ETAPA 3/4] Profiling C'
    PROFILE_IMAGE=${PROFILE_INPUT:-$INPUT}
    PROFILES="$RUN_DIR/profiles"
    LOGS="$RUN_DIR/logs"
    echo "Profiling separado das medias. Entrada: $PROFILE_IMAGE"
    echo 'Callgrind/Cachegrind podem ser lentos; PROFILE_INPUT pode apontar para uma imagem menor.'

    profile() {
        local label=$1
        shift
        echo "  [$label]"
        if ! "$@" >"$LOGS/${label}.log" 2>&1; then
            echo "  Falhou: $label (veja $LOGS/${label}.log)" >&2
        fi
    }

    if command -v gprof >/dev/null; then
        "$CC" -pg -O2 -g "$ROOT/color_quantization_cedraz/color_quant.c" -o "$RUN_DIR/bin/c_serial_gprof"
        profile c_serial_1_gprof_run bash -c 'cd "$1" && "$2" "$3" 4 "$4"' _ \
            "$PROFILES" "$RUN_DIR/bin/c_serial_gprof" "$PROFILE_IMAGE" "$OUTPUT"
        if [[ -f $PROFILES/gmon.out ]]; then
            mv -- "$PROFILES/gmon.out" "$PROFILES/c_serial_1.gmon.out"
            gprof "$RUN_DIR/bin/c_serial_gprof" "$PROFILES/c_serial_1.gmon.out" \
                > "$PROFILES/c_serial_1.gprof.txt" || true
        fi
    else
        echo '  gprof indisponivel; ignorado.'
    fi

    if command -v valgrind >/dev/null; then
        profile c_serial_1_callgrind valgrind --tool=callgrind \
            --callgrind-out-file="$PROFILES/c_serial_1.callgrind" \
            --log-file="$PROFILES/c_serial_1.callgrind.log" \
            "$RUN_DIR/bin/c_serial" "$PROFILE_IMAGE" 4 "$OUTPUT"
        if [[ -f $PROFILES/c_serial_1.callgrind ]] && command -v callgrind_annotate >/dev/null; then
            callgrind_annotate --inclusive=yes "$PROFILES/c_serial_1.callgrind" \
                > "$PROFILES/c_serial_1.callgrind_annotate.txt" || true
        fi
        profile c_serial_1_cachegrind valgrind --tool=cachegrind \
            --cachegrind-out-file="$PROFILES/c_serial_1.cachegrind" \
            --log-file="$PROFILES/c_serial_1.cachegrind.log" \
            "$RUN_DIR/bin/c_serial" "$PROFILE_IMAGE" 4 "$OUTPUT"
        if [[ -f $PROFILES/c_serial_1.cachegrind ]] && command -v cg_annotate >/dev/null; then
            cg_annotate "$PROFILES/c_serial_1.cachegrind" \
                > "$PROFILES/c_serial_1.cachegrind_annotate.txt" || true
        fi
    else
        echo '  Valgrind indisponivel; ignorado.'
    fi

    if command -v strace >/dev/null; then
        profile c_serial_1_strace strace -c -o "$PROFILES/c_serial_1.strace.txt" \
            "$RUN_DIR/bin/c_serial" "$PROFILE_IMAGE" 4 "$OUTPUT"
    else
        echo '  strace indisponivel; ignorado.'
    fi

    if command -v perf >/dev/null; then
        EVENTS=task-clock,cycles,instructions,cache-references,cache-misses,branches,branch-misses,context-switches,cpu-migrations
        profile c_serial_1_perf_stat perf stat -e "$EVENTS" \
            -o "$PROFILES/c_serial_1.perf.txt" -- "$RUN_DIR/bin/c_serial" "$PROFILE_IMAGE" 4 "$OUTPUT"
        for workers in 1 2 4 8; do
            label="c_parallel_${workers}"
            profile "${label}_perf_stat" env OMP_NUM_THREADS="$workers" perf stat -e "$EVENTS" \
                -o "$PROFILES/$label.perf.txt" -- "$RUN_DIR/bin/c_parallel" "$PROFILE_IMAGE" 4 "$OUTPUT"
            profile "${label}_perf_record" env OMP_NUM_THREADS="$workers" perf record -g \
                -o "$PROFILES/$label.perf.data" -- "$RUN_DIR/bin/c_parallel" "$PROFILE_IMAGE" 4 "$OUTPUT"
            if [[ -s $PROFILES/$label.perf.data ]]; then
                perf report --stdio -i "$PROFILES/$label.perf.data" \
                    > "$PROFILES/$label.perf_report.txt" 2> "$LOGS/${label}_perf_report.log" || true
            fi
        done
    else
        echo '  perf indisponivel; ignorado.'
    fi

    echo '[ETAPA 4/4] Profiling Python'
    if command -v strace >/dev/null; then
        profile py_serial_1_strace strace -c -o "$PROFILES/py_serial_1.strace.txt" \
            "$PYTHON" "$ROOT/quant_in_python/color_quant.py" "$PROFILE_IMAGE" 4 "$OUTPUT"
    fi
    if command -v perf >/dev/null; then
        profile py_serial_1_perf_stat perf stat -e "$EVENTS" \
            -o "$PROFILES/py_serial_1.perf.txt" -- "$PYTHON" "$ROOT/quant_in_python/color_quant.py" "$PROFILE_IMAGE" 4 "$OUTPUT"
    fi

    profile_python_case() {
        local name=$1 workers=$2 script=$3
        shift 3
        local label="${name}_${workers}"
        local -a extra=("$@")
        if command -v perf >/dev/null && [[ $name != py_serial ]]; then
            profile "${label}_perf_stat" perf stat -e "$EVENTS" \
                -o "$PROFILES/$label.perf.txt" -- "$PYTHON" "$ROOT/quant_in_python/$script" \
                "$PROFILE_IMAGE" 4 "$OUTPUT" "${extra[@]}"
            profile "${label}_perf_record" perf record -g \
                -o "$PROFILES/$label.perf.data" -- "$PYTHON" "$ROOT/quant_in_python/$script" \
                "$PROFILE_IMAGE" 4 "$OUTPUT" "${extra[@]}"
            if [[ -s $PROFILES/$label.perf.data ]]; then
                perf report --stdio -i "$PROFILES/$label.perf.data" \
                    > "$PROFILES/$label.perf_report.txt" 2> "$LOGS/${label}_perf_report.log" || true
            fi
        fi
        echo "  [${label}_cprofile]"
        if ! "$PYTHON" -m cProfile -s cumulative \
            "$ROOT/quant_in_python/$script" "$PROFILE_IMAGE" 4 "$OUTPUT" "${extra[@]}" \
            > "$PROFILES/$label.cprofile_cumulative.txt" \
            2> "$LOGS/${label}_cprofile.log"; then
            echo "  Falhou: ${label}_cprofile (veja $LOGS/${label}_cprofile.log)" >&2
        fi
    }

    profile_python_case py_serial 1 color_quant.py
    for workers in 1 2 4 8; do
        profile_python_case py_multiprocessing "$workers" color_quant_multiprocessing.py --workers "$workers"
    done
    for workers in 1 2 4 8; do
        profile_python_case py_multithreading "$workers" color_quant_multithreading.py --workers "$workers"
    done
fi

# Organiza a rodada diretamente nas pastas finais, sem script auxiliar.
RUN_ID=${RUN_DIR##*/}
DEST="$ROOT/resultados/resumos/$RUN_ID"
if [[ -e $DEST ]]; then
    echo "Destino ja existe: $DEST" >&2
    exit 1
fi
mkdir -p -- "$ROOT/resultados/resumos"
mv -- "$RUN_DIR" "$DEST"

move_case() {
    local prefix=$1 group=$2 file
    local target="$ROOT/resultados/$group/$RUN_ID"
    mkdir -p -- "$target/logs" "$target/timev" "$target/profiles"
    for file in "$DEST/logs/${prefix}"*; do mv -- "$file" "$target/logs/"; done
    for file in "$DEST/timev/${prefix}"*; do mv -- "$file" "$target/timev/"; done
    for file in "$DEST/profiles/${prefix}"*; do mv -- "$file" "$target/profiles/"; done
}

move_case c_serial_ C_results/serial
move_case c_parallel_ C_results/paralelo
move_case py_serial_ python_results/serial
move_case py_multithreading_ python_results/multithreading
move_case py_multiprocessing_ python_results/multiprocessing

mkdir -p -- "$ROOT/resultados/C_results/serial/$RUN_ID/bin" "$ROOT/resultados/C_results/paralelo/$RUN_ID/bin"
if [[ -f $DEST/bin/c_serial ]]; then mv -- "$DEST/bin/c_serial" "$ROOT/resultados/C_results/serial/$RUN_ID/bin/"; fi
if [[ -f $DEST/bin/c_serial_gprof ]]; then mv -- "$DEST/bin/c_serial_gprof" "$ROOT/resultados/C_results/serial/$RUN_ID/bin/"; fi
if [[ -f $DEST/bin/c_parallel ]]; then mv -- "$DEST/bin/c_parallel" "$ROOT/resultados/C_results/paralelo/$RUN_ID/bin/"; fi

echo "Concluido. Resultados organizados em $ROOT/resultados/"
