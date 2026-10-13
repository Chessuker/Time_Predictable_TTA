// Host driver for tests/test_baseline_pid.py: reads lines "<ref> <enc>" or
// "reset" from stdin and prints the pwm_cmd of every step, one per line.
#include <stdio.h>
#include <string.h>

#include "pid.h"

int main(void) {
  fpid_state_t s;
  fpid_init(&s);
  char line[128];
  while (fgets(line, sizeof line, stdin)) {
    if (strncmp(line, "reset", 5) == 0) {
      fpid_init(&s);
      continue;
    }
    long ref, enc;
    if (sscanf(line, "%ld %ld", &ref, &enc) != 2) {
      fprintf(stderr, "bad line: %s", line);
      return 1;
    }
    printf("%ld\n", (long)fpid_step(&s, (int32_t)ref, (int32_t)enc));
  }
  return 0;
}
