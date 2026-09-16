/* Fixed inference kernels. Model files contain data, never generated code. */
#include <math.h>
#ifdef _WIN32
#define API __declspec(dllexport)
#else
#define API
#endif

static double sigmoid(double x) {
    return 1.0 / (1.0 + exp(-fmax(-60.0, fmin(60.0, x))));
}

API void tracker_trees(const double *nodes, const int *offsets, int trees,
                      double intercept, const double *x, int width, int rows,
                      double *out) {
    for (int r = 0; r < rows; r++) {
        double score = intercept;
        for (int t = 0; t < trees; t++) {
            const double *tree = nodes + offsets[t];
            int i = 0;
            while (tree[i * 5] != -2) {
                const double *node = tree + i * 5;
                float value = (float)x[r * width + (int)node[0]];
                i = (int)node[value <= node[1] ? 2 : 3];
            }
            score += tree[i * 5 + 4];
        }
        out[r] = sigmoid(score);
    }
}

API void tracker_dense(const double *weights, const double *bias,
                      const int *widths, int layers, const double *x,
                      int rows, double *out) {
    for (int r = 0; r < rows; r++) {
        double first[64], second[64];
        const double *input = x + r * widths[0];
        int weight_offset = 0, bias_offset = 0;
        for (int layer = 0; layer < layers; layer++) {
            double *output = layer % 2 == 0 ? first : second;
            int ins = widths[layer], outs = widths[layer + 1];
            for (int n = 0; n < outs; n++) {
                double value = 0;
                for (int j = 0; j < ins; j++)
                    value += weights[weight_offset + n * ins + j] * input[j];
                value += bias[bias_offset + n];
                output[n] = layer + 1 < layers ? fmax(0, value) : value;
            }
            weight_offset += ins * outs;
            bias_offset += outs;
            input = output;
        }
        out[r] = sigmoid(input[0]);
    }
}
