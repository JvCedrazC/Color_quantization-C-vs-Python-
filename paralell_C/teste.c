#include <stdio.h>
#include <stdlib.h>
#include <omp.h>

enum { TAMANHO_VETOR = 1000000 };

int main(void) {
    int *vetor = malloc((size_t)TAMANHO_VETOR * sizeof *vetor);

    if (vetor == NULL) {
        fprintf(stderr, "Erro ao alocar o vetor.\n");
        return EXIT_FAILURE;
    }

    #pragma omp parallel for
    for (int i = 0; i < TAMANHO_VETOR; i++) {
        vetor[i] = i % 100;
    }

   

    printf("Ultimo valor: vetor[%d] = %d\n",
           TAMANHO_VETOR - 1, vetor[TAMANHO_VETOR - 1]);

    free(vetor);
    return EXIT_SUCCESS;
}
